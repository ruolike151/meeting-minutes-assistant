"""待确认区接口（产品文档 5.1 / M4 人在回路的前置接口）。

待确认条目的读取是「纪要 + 人工修改合并」的结果，
确认与丢弃都写 override 表，绝不改动模型生成的纪要原文。
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from app.agent.answers import AnswerContext
from app.api.deps import get_context
from app.api.schemas import ReviewItem, ReviewResolveResponse, ReviewResolveRequest
from app.domain.models import global_item_id, split_item_id
from app.store.minutes_repo import MinutesRepo, OverrideRepo
from app.store.repos import MeetingRepo
from app.tools.registry import ToolContext

router = APIRouter(prefix="/api/review", tags=["review"])


def build_review_items(
    conn: sqlite3.Connection, meeting_id: str | None = None
) -> list[ReviewItem]:
    """列出需要人工确认的条目，可限定到单场会议。"""
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
        for item in minutes.action_items:
            if not item.needs_review:
                continue
            items.append(
                ReviewItem(
                    id=global_item_id(meeting.id, item.id),
                    meeting_id=meeting.id,
                    meeting_title=meeting.title,
                    task=item.task,
                    owner=item.owner,
                    owner_name=meeting.speaker_name(item.owner) if item.owner else "未指定",
                    due_text=item.due_text,
                    reason=item.review_reason or "信息不完整",
                    confidence=item.confidence,
                    citations=ctx.citations(meeting.id, item.evidence),
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
        meeting_id, local_id = split_item_id(item_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    minutes = MinutesRepo(ctx.conn).get_with_overrides(meeting_id)
    if minutes is None or not any(item.id == local_id for item in minutes.action_items):
        raise HTTPException(status_code=404, detail="待确认条目不存在")

    OverrideRepo(ctx.conn).upsert(
        meeting_id,
        local_id,
        resolution="confirmed" if payload.decision == "confirm" else "dismissed",
        owner=payload.owner,
        due=payload.due,
        note=payload.note,
    )
    return ReviewResolveResponse(
        item_id=item_id,
        decision=payload.decision,
        message="已确认，条目已并入最终纪要"
        if payload.decision == "confirm"
        else "已忽略该条目",
    )
