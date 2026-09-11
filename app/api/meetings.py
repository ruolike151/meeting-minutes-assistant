"""会议库与纪要接口。

路由只做三件事：校验入参、调用工具层、把结果转成响应 DTO。
业务规则一律不写在这里（《技术方案》3 节分层规则第 4 条）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_context
from app.api.review import build_review_items
from app.api.schemas import ActionItemPatch, MeetingDetail
from app.store.minutes_repo import MinutesRepo
from app.store.repos import MeetingRepo, SegmentRepo
from app.tools.meetings import (
    GetMinutesInput,
    GetMinutesOutput,
    ListMeetingsInput,
    ListMeetingsOutput,
    MinutesDetail,
    UpdateActionItemInput,
    UpdateActionItemOutput,
)
from app.tools.registry import ToolContext, call_tool

router = APIRouter(prefix="/api", tags=["meetings"])


def _call(ctx: ToolContext, name: str, payload):
    """调用工具并把「ID 格式错」这类入参问题转成 400，而不是 500。"""
    try:
        return call_tool(ctx, name, payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/meetings", response_model=ListMeetingsOutput)
def list_meetings(
    q: str | None = Query(default=None, description="标题或参会人"),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    participant: str | None = Query(default=None),
    ctx: ToolContext = Depends(get_context),
) -> ListMeetingsOutput:
    payload = ListMeetingsInput(
        q=q, date_from=date_from, date_to=date_to, participant=participant
    )
    return _call(ctx, "list_meetings", payload)


@router.get("/meetings/{meeting_id}", response_model=MeetingDetail)
def get_meeting(meeting_id: str, ctx: ToolContext = Depends(get_context)) -> MeetingDetail:
    meeting = MeetingRepo(ctx.conn).get(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="会议不存在")
    minutes = MinutesRepo(ctx.conn).get_with_overrides(meeting_id)
    return MeetingDetail(
        id=meeting.id,
        title=meeting.title,
        date=meeting.date,
        time=meeting.time,
        duration=meeting.duration,
        status=meeting.status,
        audio_file=meeting.audio_file,
        participants=meeting.participants,
        segments=SegmentRepo(ctx.conn).list_for_meeting(meeting_id),
        summary=minutes.summary if minutes else [],
        decisions=minutes.decisions if minutes else [],
        action_items=minutes.action_items if minutes else [],
        open_questions=minutes.open_questions if minutes else [],
        risks=minutes.risks if minutes else [],
        review_items=build_review_items(ctx.conn, meeting_id),
    )


@router.get("/meetings/{meeting_id}/minutes", response_model=MinutesDetail)
def get_minutes(meeting_id: str, ctx: ToolContext = Depends(get_context)) -> MinutesDetail:
    result: GetMinutesOutput = _call(ctx, "get_minutes", GetMinutesInput(meeting_id=meeting_id))
    if not result.found or result.minutes is None:
        raise HTTPException(status_code=404, detail="纪要不存在")
    return result.minutes


@router.patch("/action-items/{item_id}", response_model=UpdateActionItemOutput)
def patch_action_item(
    item_id: str, payload: ActionItemPatch, ctx: ToolContext = Depends(get_context)
) -> UpdateActionItemOutput:
    result: UpdateActionItemOutput = _call(
        ctx,
        "update_action_item",
        UpdateActionItemInput(
            item_id=item_id,
            status=payload.status,
            owner=payload.owner,
            due=payload.due,
        ),
    )
    if not result.found:
        raise HTTPException(status_code=404, detail=result.message or "待办不存在")
    return result
