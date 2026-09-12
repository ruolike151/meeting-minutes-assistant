"""minutes 图的四个节点（M4 T2）：生成 → 校验 → 人工闸门 → 收尾。

节点一律是 `(state, context)` 纯函数，不 import LangGraph：

* 挂起由 `context.interrupt_fn` 注入（`graph.py` 传 `langgraph.types.interrupt`），
  测试里换成记录调用的假函数即可断言闸门行为；
* 昂贵的工作（LLM 生成、向量索引）只在 `generate` 里做一次；
  `review_gate` 只读状态与数据库——`interrupt` 恢复时框架会**重新执行包含它的节点**，
  所以闸门节点绝不能有副作用，否则恢复一次就多花一次钱（《M4 计划》4.1）。

落库时机是刻意选的：`generate` 就写库（会议 + 片段 + 纪要 + 状态），
因为挂起期间待确认区要能从数据库读到这些条目。`finalize` 只做状态翻转。
"""

from __future__ import annotations

import time

from app.agent.minutes import generate_meeting_minutes
from app.agent.nodes.context import NodeContext, append_trace, ms
from app.agent.review import apply_status, pending_list, review_enabled, review_status
from app.domain.models import Minutes
from app.retrieval.index import index_meeting
from app.store.minutes_repo import MinutesRepo
from app.store.repos import MeetingRepo


def generate_minutes_node(state, context: NodeContext) -> dict:
    """调 LLM 生成纪要、落库、建向量索引。"""
    started = time.perf_counter()
    meeting = state["meeting"]
    segments = list(state.get("segments") or [])
    fallback = state.get("minutes") or MinutesRepo(context.conn).get(meeting.id)
    notes = list(state.get("notes") or [])

    minutes, generated_notes = generate_meeting_minutes(
        context.settings,
        context.conn,
        meeting,
        segments,
        fallback=fallback,
    )
    notes.extend(generated_notes)

    # 先落库、再算待确认：待确认清单的口径是"数据库里的纪要 + override"，
    # 顺序反过来会读到还没写进去的纪要，算出 0 条（这个坑踩过一次，用例守住）。
    stored = meeting.model_copy(update={"status": "processing"})
    MeetingRepo(context.conn).create(stored, segments, minutes)
    status, pending = review_status(context.conn, meeting.id, context.settings)
    apply_status(context.conn, meeting.id, status)
    stored = stored.model_copy(update={"status": status})
    index_note = index_meeting(context.settings, context.conn, meeting.id)
    if index_note:
        notes.append(index_note)

    return {
        "meeting": stored,
        "minutes": minutes,
        "notes": notes,
        "pending_count": pending,
        "status": status,
        "trace": append_trace(
            state, "generate_minutes", started, "待确认 %d 条" % pending
        ),
    }


def validate_minutes_node(state, context: NodeContext) -> dict:
    """校验生成结果是否可用；不可用则计数交给条件边重试（最多 2 次）。"""
    started = time.perf_counter()
    minutes = state.get("minutes")
    ok = isinstance(minutes, Minutes)
    retry_count = int(state.get("retry_count") or 0)
    if not ok:
        retry_count += 1
    return {
        "retry_count": retry_count,
        "trace": append_trace(
            state, "validate_minutes", started, "通过" if ok else "非法，准备重试"
        ),
    }


def review_gate_node(state, context: NodeContext) -> dict:
    """人工闸门：有待确认条目就挂起，等 `POST /api/review/{id}/resolve` 恢复。

    三种情况：
    * `MA_REVIEW_MODE=off`（消融）：不挂起，直接放行；
    * 待确认为空：不挂起，直接放行；
    * 有待确认条目且注入了挂起函数：`interrupt(payload)` —— 图在此处暂停，
      状态落检查点，后台 worker 线程正常结束。
    """
    started = time.perf_counter()
    meeting_id = state["meeting_id"]
    pending = pending_list(context.conn, meeting_id, settings=context.settings)
    payload = {
        "meeting_id": meeting_id,
        "count": len(pending),
        "items": [
            {"id": item.id, "kind": item.kind, "text": item.text, "reason": item.reason}
            for item in pending
        ],
    }
    if not review_enabled(context.settings) or not pending:
        return {
            "pending_count": len(pending),
            "review_payload": payload,
            "trace": append_trace(state, "review_gate", started, "无需人工确认"),
        }
    if context.interrupt_fn is None:
        # 没有挂起能力（例如直接调用节点）时不阻塞流程，交由收尾节点决定状态
        return {
            "pending_count": len(pending),
            "review_payload": payload,
            "trace": append_trace(state, "review_gate", started, "无挂起能力，跳过"),
        }
    resumed = context.interrupt_fn(payload)
    return {
        "pending_count": len(pending),
        "review_payload": payload,
        "resume_value": resumed,
        "trace": append_trace(state, "review_gate", started, "人工已处理"),
    }


def finalize_minutes_node(state, context: NodeContext) -> dict:
    """收尾：还有待确认条目就停在 `awaiting_review`，否则转 `processed`。"""
    started = time.perf_counter()
    meeting_id = state["meeting_id"]
    status, pending = review_status(context.conn, meeting_id, context.settings)
    apply_status(context.conn, meeting_id, status)
    return {
        "status": status,
        "pending_count": pending,
        "trace": append_trace(state, "finalize_minutes", started, status),
    }


def minutes_retry_decision(state) -> str:
    """条件边：非法且还没用满重试次数就回去重生成，否则去闸门。"""
    if state.get("minutes") is None and int(state.get("retry_count") or 0) < 2:
        return "generate"
    return "review_gate"


__all__ = [
    "finalize_minutes_node",
    "generate_minutes_node",
    "minutes_retry_decision",
    "ms",
    "review_gate_node",
    "validate_minutes_node",
]
