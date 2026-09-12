"""人在回路的挂起与恢复（M4 T2 / T3，《技术方案》7.4）。

三个纯函数加一个薄门面，职责分开：

* `pending_items` / `has_pending`：判据来自业务库（`minutes` + override），
  不依赖检查点——这是"checkpoint 丢了也能收尾"的基础；
* `resolve`：写 override + 审计（同事务），然后尝试收尾；
* `resume_if_ready`：待确认为空时，先尝试恢复挂起的 minutes 图
  （`Command(resume=…)`），无论恢复成功与否都保证会议被收尾（幂等）。

恢复为什么要降级：检查点库是框架 schema，可能被清理、可能换机器、也可能
`MA_CHECKPOINT_BACKEND=off`。协同流程的正确性不能绑在检查点上——挂起是编排能力，
收尾是业务事实。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.agent.checkpoint import checkpoint_config, open_checkpointer
from app.config import Settings
from app.domain.models import split_item_id
from app.store.db import connect
from app.store.minutes_repo import MinutesRepo, OverrideRepo, pending_items
from app.store.repos import MeetingRepo
from app.store.review_repo import ReviewEventRepo


class ReviewError(ValueError):
    """待确认条目的入参问题（不存在 / 动作非法），接口层转 400/404。"""


@dataclass(frozen=True)
class ResolveResult:
    item_id: str
    meeting_id: str
    action: str
    remaining: int
    finalized: bool
    message: str


def has_pending(
    conn: sqlite3.Connection, meeting_id: str, *, settings: Settings | None = None
) -> bool:
    return bool(pending_list(conn, meeting_id, settings=settings))


def pending_list(
    conn: sqlite3.Connection, meeting_id: str, *, settings: Settings | None = None
):
    """按配置的阈值与上限取待确认清单。"""
    threshold = settings.review_confidence if settings is not None else 0.70
    limit = settings.review_max_items if settings is not None else None
    return pending_items(conn, meeting_id, confidence_threshold=threshold, limit=limit)


def review_enabled(settings: Settings | None) -> bool:
    """`MA_REVIEW_MODE=off` 时不做闸门（生成完直接收尾，M5 消融用）。"""
    if settings is None:
        return True
    return (settings.review_mode or "gate").lower() != "off"


def review_status(
    conn: sqlite3.Connection, meeting_id: str, settings: Settings | None
) -> tuple[str, int]:
    """会议该处于哪个状态（`awaiting_review` / `processed`）以及待确认条数。

    闸门关闭（`MA_REVIEW_MODE=off`）时即使有条目也不再挂起——那是 M5 的消融开关，
    用来量化"人在回路"带来的质量差异。
    """
    pending = len(pending_list(conn, meeting_id, settings=settings))
    if pending and review_enabled(settings):
        return "awaiting_review", pending
    return "processed", pending


def apply_status(conn: sqlite3.Connection, meeting_id: str, status: str) -> None:
    """把状态写进会议行（`awaiting_review` 与 `processed` 两条路径）。"""
    if status == "awaiting_review":
        mark_awaiting_review(conn, meeting_id)
    else:
        finalize_meeting(conn, meeting_id, reason="最终稿")


def refresh_status(
    conn: sqlite3.Connection, meeting_id: str, settings: Settings | None
) -> str:
    """按当前待确认清单刷新会议状态，返回最终状态（重新生成纪要用它）。"""
    status, _pending = review_status(conn, meeting_id, settings)
    apply_status(conn, meeting_id, status)
    return status


def resolve(
    conn: sqlite3.Connection,
    settings: Settings | None,
    item_id: str,
    action: str,
    *,
    owner: str | None = None,
    due: str | None = None,
    note: str | None = None,
) -> ResolveResult:
    """确认 / 忽略 / 修改一条待确认条目，并尝试收尾整场会议。"""
    if action not in ("confirm", "dismiss", "edit"):
        raise ReviewError("不支持的动作：%s" % action)
    try:
        meeting_id, local_id = split_item_id(item_id)
    except ValueError as error:
        raise ReviewError(str(error)) from error
    if MeetingRepo(conn).get(meeting_id) is None:
        raise ReviewError("会议不存在：%s" % meeting_id)

    minutes = MinutesRepo(conn).get_with_overrides(meeting_id)
    if minutes is None:
        raise ReviewError("这场会议还没有纪要")
    entry = next(
        (
            item
            for item in pending_items(conn, meeting_id)
            if item.id == local_id
        ),
        None,
    )
    if entry is None:
        # 已经处理过的条目再点一次不算错误（前端重试 / 双击常见）：
        # 只要本次动作与已记录的结论一致，就按幂等返回，不重复写审计。
        prior = OverrideRepo(conn).get(meeting_id, local_id)
        if prior and _same_action(prior, action, owner=owner, due=due):
            remaining = len(pending_list(conn, meeting_id, settings=settings))
            return ResolveResult(
                item_id=item_id,
                meeting_id=meeting_id,
                action=action,
                remaining=remaining,
                finalized=not remaining,
                message="该条目已处理过，无需重复操作",
            )
        raise ReviewError("待确认条目不存在：%s" % item_id)

    overrides = OverrideRepo(conn)
    before = overrides.get(meeting_id, local_id) or {}
    changes: dict = {}
    if action == "edit":
        if not entry.editable:
            raise ReviewError("决议没有可修改的字段，只能确认或忽略")
        if owner:
            changes["owner"] = owner
        if due:
            changes["due"] = due
        if not changes:
            raise ReviewError("edit 动作至少要带 owner 或 due")
    else:
        changes["resolution"] = "confirmed" if action == "confirm" else "dismissed"
        if owner:
            changes["owner"] = owner
        if due:
            changes["due"] = due
        if note:
            changes["note"] = note

    after = overrides.upsert(meeting_id, local_id, **changes)
    # 审计与 override 同一次提交：不允许出现"改了但没留痕"
    ReviewEventRepo(conn).record(
        meeting_id=meeting_id,
        item_id=local_id,
        kind=entry.kind,
        action=action,
        before=before,
        after=after,
    )
    conn.commit()

    finalized = resume_if_ready(conn, settings, meeting_id)
    remaining = len(pending_list(conn, meeting_id, settings=settings))
    if finalized:
        message = "已确认，纪要已完成" if action == "confirm" else "已处理，纪要已完成"
    elif remaining:
        message = "已处理，还剩 %d 条待确认" % remaining
    else:
        message = "已处理"
    return ResolveResult(
        item_id=item_id,
        meeting_id=meeting_id,
        action=action,
        remaining=remaining,
        finalized=finalized,
        message=message,
    )


def finalize_meeting(conn: sqlite3.Connection, meeting_id: str, *, reason: str = "") -> bool:
    """把会议置为已收尾（幂等）。返回值表示本次是否真的改了状态。"""
    meeting = MeetingRepo(conn).get(meeting_id)
    if meeting is None:
        return False
    if meeting.status == "processed" and not reason:
        return False
    conn.execute(
        "UPDATE meetings SET status = ? WHERE id = ?", ("processed", meeting_id)
    )
    conn.commit()
    return True


def _same_action(
    override: dict, action: str, *, owner: str | None, due: str | None
) -> bool:
    """判断这次动作是否与已记录的结论等价（幂等判据）。"""
    resolution = (override or {}).get("resolution")
    if action == "confirm":
        return resolution == "confirmed"
    if action == "dismiss":
        return resolution == "dismissed"
    if action == "edit":
        if owner and (override or {}).get("owner") != owner:
            return False
        if due and (override or {}).get("due") != due:
            return False
        return bool(owner or due)
    return False


def mark_awaiting_review(conn: sqlite3.Connection, meeting_id: str) -> None:
    """会议进入等待人工确认。任务与会议都不再往下走，但不阻塞 worker。"""
    conn.execute(
        "UPDATE meetings SET status = ? WHERE id = ?", ("awaiting_review", meeting_id)
    )
    conn.commit()


def resume_if_ready(
    conn: sqlite3.Connection, settings: Settings | None, meeting_id: str
) -> bool:
    """待确认为空时恢复挂起的图并收尾；返回是否已收尾。"""
    if pending_list(conn, meeting_id, settings=settings):
        return False
    # 先尝试真正的图恢复（有检查点时走 Command(resume=…)），
    # 再落业务事实：恢复成功与否都必须保证会议被收尾（finalize 幂等）。
    _resume_graph(conn, settings, meeting_id)
    finalize_meeting(conn, meeting_id, reason="resume")
    return True


def _resume_graph(
    conn: sqlite3.Connection, settings: Settings | None, meeting_id: str
) -> bool:
    """尝试用 `Command(resume=…)` 恢复挂起的 minutes 图。

    任何一步不可用（没有 settings、没有检查点、框架异常）都返回 False，
    由调用方走直接收尾的降级路径。
    """
    if settings is None:
        return False
    saver = open_checkpointer(settings)
    if saver is None:
        return False
    try:
        from app.agent.graph import build_minutes_graph, resume_input
        from app.agent.nodes import NodeContext

        context = NodeContext.from_settings(conn, settings)
        graph = build_minutes_graph(context, checkpointer=saver)
        config = checkpoint_config(meeting_id)
        snapshot = graph.get_state(config)
        if not getattr(snapshot, "values", None):
            return False  # 没有这个 thread 的检查点（例如检查点库被清理过）
        graph.invoke(resume_input({"meeting_id": meeting_id, "approved": True}), config)
        return True
    except Exception:  # noqa: BLE001 - 恢复失败必须降级，而不是让确认接口 500
        return False


def meeting_connection(settings: Settings) -> sqlite3.Connection:
    """接口层自己开连接时用（SSE / 后台线程不适合共享请求连接）。"""
    return connect(settings.db_path)


__all__ = [
    "ResolveResult",
    "ReviewError",
    "apply_status",
    "finalize_meeting",
    "has_pending",
    "mark_awaiting_review",
    "meeting_connection",
    "pending_list",
    "refresh_status",
    "resolve",
    "review_status",
    "resume_if_ready",
    "review_enabled",
]
