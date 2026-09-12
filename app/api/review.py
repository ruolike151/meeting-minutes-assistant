"""待确认区接口（产品文档 5.1 ④ / 《M4 计划》T3 T4）。

三条纪律：

1. 待确认清单只有一个来源：`store.minutes_repo.pending_items`
   （待办 + 低置信度决议），会议列表的 review_count 与这里共用同一口径；
2. 人工动作只写 `action_item_overrides`（结论）与 `review_events`（审计），
   绝不改动模型生成的纪要原文；
3. 处理完最后一条时恢复挂起的 minutes 图（`Command(resume=…)`），
   没有检查点时降级为直接收尾——接口永不 5xx。
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from app.agent.answers import AnswerContext
from app.agent.review import ReviewError, resolve
from app.api.deps import get_context
from app.api.schemas import ReviewItem, ReviewResolveRequest, ReviewResolveResponse
from app.domain.models import global_item_id
from app.store.minutes_repo import MinutesRepo
from app.store.minutes_repo import pending_items as list_pending_items
from app.store.repos import MeetingRepo
from app.tools.registry import ToolContext

router = APIRouter(prefix="/api/review", tags=["review"])


def build_review_items(
    conn: sqlite3.Connection, meeting_id: str | None = None
) -> list[ReviewItem]:
    """列出需要人工确认的条目（待办 + 决议），可限定到单场会议。"""
    meeting_repo = MeetingRepo(conn)
    meetings = (
        [meeting_repo.get(meeting_id)] if meeting_id else meeting_repo.list()
    )
    items: list[ReviewItem] = []
    for meeting in meetings:
        if meeting is None:
            continue
        minutes = MinutesRepo(conn).get_with_overrides(meeting.id)
        if minutes is None:
            continue
        ctx = AnswerContext(conn=conn, meetings=[meeting])
        for entry in list_pending_items(conn, meeting.id):
            items.append(
                ReviewItem(
                    id=global_item_id(meeting.id, entry.id),
                    meeting_id=meeting.id,
                    meeting_title=meeting.title,
                    kind=entry.kind,
                    task=entry.text,
                    owner=entry.owner,
                    owner_name=meeting.speaker_name(entry.owner) if entry.owner else "未指定",
                    due=entry.due,
                    due_text=entry.due_text,
                    reason=entry.reason,
                    confidence=entry.confidence,
                    editable=entry.editable,
                    citations=ctx.citations(meeting.id, entry.evidence),
                )
            )
    return items


@router.get("", response_model=list[ReviewItem])
def list_review_items(
    meeting_id: str | None = Query(default=None),
    ctx: ToolContext = Depends(get_context),
) -> list[ReviewItem]:
    return build_review_items(ctx.conn, meeting_id)


@router.post("/{item_id}/resolve", response_model=ReviewResolveResponse)
def resolve_review_item(
    item_id: str,
    payload: ReviewResolveRequest,
    ctx: ToolContext = Depends(get_context),
) -> ReviewResolveResponse:
    try:
        result = resolve(
            ctx.conn,
            ctx.settings,
            item_id,
            payload.resolved_action(),
            owner=payload.owner,
            due=payload.due,
            note=payload.note,
        )
    except ReviewError as error:
        detail = str(error)
        status = 404 if "不存在" in detail else 400
        raise HTTPException(status_code=status, detail=detail) from error

    return ReviewResolveResponse(
        item_id=item_id,
        decision=result.action,
        meeting_id=result.meeting_id,
        remaining=result.remaining,
        finalized=result.finalized,
        message=result.message,
    )


@router.get("/{item_id}/events", response_model=list[dict])
def list_item_events(
    item_id: str, ctx: ToolContext = Depends(get_context)
) -> list[dict]:
    """单条待确认条目的处理流水（人工动作审计，M4 T5）。"""
    from app.domain.models import split_item_id
    from app.store.review_repo import ReviewEventRepo

    try:
        meeting_id, local_id = split_item_id(item_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    events = ReviewEventRepo(ctx.conn).list_for_item(meeting_id, local_id)
    return [
        {
            "id": event.id,
            "action": event.action,
            "before": event.before,
            "after": event.after,
            "actor": event.actor,
            "created_at": event.created_at,
        }
        for event in events
    ]
