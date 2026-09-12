"""纪要仓储与人工修改合并。

两个关键点（《技术方案》6.2）：
1. 纪要正文整份 JSON 存 `minutes.payload`，Pydantic 负责校验；
2. 人工修改单独存 `action_item_overrides`，读取时合并，
   因此重新生成纪要不会把人工确认的结果覆盖掉。

override 的 `resolution` 有三种状态，对应人在回路的三个动作：
confirmed（确认并入最终稿）、dismissed（丢弃）、pending（重新挂起待确认）。

M4 追加两件事：
* `pending_items`：待确认清单的唯一计算口径（待办 + 低置信度决议），
  会议列表的 review_count、编排层的挂起判据、接口层的待确认区都调它，
  避免三处各写一遍判断而漂移；
* override 同样作用于决议（决议没有负责人与截止时间，只认 resolution）。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.models import (
    ActionItem,
    Decision,
    Meeting,
    Minutes,
    REVIEW_CONFIDENCE_THRESHOLD,
)
from app.store.repos import MeetingRepo, iso, parse_date, utcnow

@dataclass(frozen=True)
class PendingItem:
    """待确认条目：把待办与决议统一成一种形状，供接口层与编排层共用。"""

    kind: str                     # action_item | decision
    id: str                       # 纪要内部的条目 ID（不是全局 ID）
    text: str
    confidence: float
    reason: str
    owner: str | None = None
    owner_source: str = "unknown"
    due: "object | None" = None   # date | None
    due_text: str | None = None
    evidence: list[str] = field(default_factory=list)

    @property
    def editable(self) -> bool:
        """决议没有负责人与截止时间可改，只有待办可编辑。"""
        return self.kind == "action_item"


class MinutesRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, minutes: Minutes) -> Minutes:
        updated_at = minutes.updated_at or utcnow()
        self.conn.execute(
            "INSERT OR REPLACE INTO minutes(meeting_id, payload, schema_version, updated_at)"
            " VALUES(?,?,?,?)",
            (
                minutes.meeting_id,
                json.dumps(minutes.model_dump(mode="json"), ensure_ascii=False),
                minutes.schema_version,
                iso(updated_at),
            ),
        )
        self.conn.commit()
        return minutes.model_copy(update={"updated_at": updated_at})

    def get(self, meeting_id: str) -> Minutes | None:
        row = self.conn.execute(
            "SELECT payload, updated_at FROM minutes WHERE meeting_id = ?", (meeting_id,)
        ).fetchone()
        if row is None:
            return None
        minutes = Minutes.model_validate(json.loads(row["payload"]))
        return minutes.model_copy(update={"updated_at": datetime.fromisoformat(row["updated_at"])})

    def get_with_overrides(self, meeting_id: str) -> Minutes | None:
        """读取纪要并合并人工修改：override 优先于模型生成结果。"""
        minutes = self.get(meeting_id)
        if minutes is None:
            return None
        return apply_overrides(minutes, OverrideRepo(self.conn).list_for_meeting(meeting_id))

    def action_items(self, meeting_id: str) -> list[ActionItem]:
        minutes = self.get_with_overrides(meeting_id)
        return list(minutes.action_items) if minutes else []

    def list_all_action_items(self) -> list[tuple[Meeting, ActionItem]]:
        rows = self.conn.execute("SELECT meeting_id FROM minutes ORDER BY meeting_id").fetchall()
        result: list[tuple[Meeting, ActionItem]] = []
        for row in rows:
            meeting = MeetingRepo(self.conn).get(row["meeting_id"])
            if meeting is None:
                continue
            for item in self.action_items(row["meeting_id"]):
                result.append((meeting, item))
        return result


class OverrideRepo:
    """人工修改：确认 / 改负责人 / 改截止时间 / 标记完成 / 丢弃。"""

    FIELDS = ("status", "owner", "due", "resolution", "note")

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(self, meeting_id: str, item_id: str, **changes: str | None) -> dict:
        current = self.get(meeting_id, item_id) or {"meeting_id": meeting_id, "item_id": item_id}
        for field in self.FIELDS:
            if field in changes:
                current[field] = changes[field]
        self.conn.execute(
            "INSERT INTO action_item_overrides"
            "(meeting_id, item_id, status, owner, due, resolution, note, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)"
            " ON CONFLICT(meeting_id, item_id) DO UPDATE SET"
            " status=excluded.status, owner=excluded.owner, due=excluded.due,"
            " resolution=excluded.resolution, note=excluded.note,"
            " updated_at=excluded.updated_at",
            (
                meeting_id,
                item_id,
                current.get("status"),
                current.get("owner"),
                current.get("due"),
                current.get("resolution"),
                current.get("note"),
                iso(),
            ),
        )
        self.conn.commit()
        return current

    def get(self, meeting_id: str, item_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM action_item_overrides WHERE meeting_id = ? AND item_id = ?",
            (meeting_id, item_id),
        ).fetchone()
        return dict(row) if row else None

    def list_for_meeting(self, meeting_id: str) -> dict[str, dict]:
        rows = self.conn.execute(
            "SELECT * FROM action_item_overrides WHERE meeting_id = ?", (meeting_id,)
        ).fetchall()
        return {row["item_id"]: dict(row) for row in rows}

    def list_all(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM action_item_overrides").fetchall()
        return [dict(row) for row in rows]


def apply_overrides(minutes: Minutes, overrides: dict[str, dict]) -> Minutes:
    """把人工修改合并进纪要。

    * resolution=confirmed：条目并入最终稿，退出待确认区；
    * resolution=dismissed：条目被丢弃；
    * resolution=pending：条目重新挂起，等人工处理（flag_uncertainty 用）；
    * status / owner / due 直接覆盖生成结果。

    确认不等于改写置信度：模型原本的 confidence 原样保留，
    只是把把关的人从置信度阈值换成了人工。

    M4 起决议也走同一张表：`resolution=dismissed` 的决议从纪要里移除，
    `confirmed` 的决议解除待确认标记（决议没有 status / owner / due 可改）。
    """
    if not overrides:
        return minutes
    items: list[ActionItem] = []
    for item in minutes.action_items:
        override = overrides.get(item.id)
        if override is None:
            items.append(item)
            continue
        resolution = override.get("resolution")
        if resolution == "dismissed":
            continue
        changes: dict = {}
        if override.get("status"):
            changes["status"] = override["status"]
        if override.get("owner"):
            changes["owner"] = override["owner"]
            if item.owner_source in ("missing", "unknown", "inferred"):
                # 人工补录负责人后来源改为 confirmed，便于区分模型判断与人工输入
                changes["owner_source"] = "confirmed"
        if override.get("due"):
            changes["due"] = parse_date(override["due"])
        if resolution == "confirmed":
            changes["needs_review"] = False
            changes["confirmed"] = True
            changes["review_reason"] = None
        elif resolution == "pending":
            changes["needs_review"] = True
            changes["review_reason"] = (
                override.get("note") or item.review_reason or "人工标记为待确认"
            )
        items.append(item.model_copy(update=changes))
    decisions: list[Decision] = []
    for decision in minutes.decisions:
        override = overrides.get(decision.id)
        if override is None:
            decisions.append(decision)
            continue
        resolution = override.get("resolution")
        if resolution == "dismissed":
            continue
        changes: dict = {}
        if resolution == "confirmed":
            changes["needs_review"] = False
            changes["confirmed"] = True
            changes["review_reason"] = None
        elif resolution == "pending":
            changes["needs_review"] = True
            changes["review_reason"] = (
                override.get("note") or decision.review_reason or "人工标记为待确认"
            )
        decisions.append(decision.model_copy(update=changes) if changes else decision)
    return minutes.model_copy(update={"action_items": items, "decisions": decisions})


def review_items(minutes: Minutes) -> list[ActionItem]:
    """待确认区条目：needs_review 为真且尚未被确认 / 丢弃。"""
    return [item for item in minutes.action_items if item.needs_review]


def pending_items(
    conn: sqlite3.Connection,
    meeting_id: str,
    *,
    confidence_threshold: float = REVIEW_CONFIDENCE_THRESHOLD,
    limit: int | None = None,
) -> list[PendingItem]:
    """待确认清单的唯一口径（《M4 计划》5.2）。

    判据**只看合并 override 之后的结果**，因此：

    * 人工确认过的条目不会再次挂起；
    * 判据可以从数据库重建（`minutes` + `action_item_overrides`），
      checkpoint 丢了也照样算得出"还剩几条"。

    覆盖范围（决策 ②=A）：待办（`needs_review`）+ 低置信度决议。
    """
    minutes = MinutesRepo(conn).get_with_overrides(meeting_id)
    if minutes is None:
        return []
    result: list[PendingItem] = []
    for item in minutes.action_items:
        if not item.needs_review:
            continue
        result.append(
            PendingItem(
                kind="action_item",
                id=item.id,
                text=item.task,
                confidence=item.confidence,
                reason=item.review_reason or "负责人或截止时间待补充",
                owner=item.owner,
                owner_source=item.owner_source,
                due=item.due,
                due_text=item.due_text,
                evidence=list(item.evidence),
            )
        )
    for decision in minutes.decisions:
        if decision.confirmed:
            continue
        if decision.needs_review or decision.confidence < confidence_threshold:
            result.append(
                PendingItem(
                    kind="decision",
                    id=decision.id,
                    text=decision.content,
                    confidence=decision.confidence,
                    reason=decision.review_reason
                    or "置信度 %.2f 低于阈值 %.2f，请人工确认"
                    % (decision.confidence, confidence_threshold),
                    evidence=list(decision.evidence),
                )
            )
    if limit is not None and limit > 0:
        return result[:limit]
    return result
