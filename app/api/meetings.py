"""会议库与纪要接口。

路由只做三件事：校验入参、调用工具层、把结果转成响应 DTO。
业务规则一律不写在这里（《技术方案》3 节分层规则第 4 条）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.deps import get_context
from app.api.review import build_review_items
from app.api.schemas import ActionItemPatch, MeetingDetail
from app.agent.minutes import generate_meeting_minutes
from app.agent.review import refresh_status
from app.store.files import audio_path
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
    status: str | None = Query(
        default=None, description="按处理状态过滤：processing / awaiting_review / processed"
    ),
    ctx: ToolContext = Depends(get_context),
) -> ListMeetingsOutput:
    payload = ListMeetingsInput(
        q=q, date_from=date_from, date_to=date_to, participant=participant
    )
    result: ListMeetingsOutput = _call(ctx, "list_meetings", payload)
    if status:
        result = ListMeetingsOutput(
            meetings=[item for item in result.meetings if item.status == status]
        )
    return result


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


@router.get("/meetings/{meeting_id}/audio")
def get_meeting_audio(meeting_id: str, ctx: ToolContext = Depends(get_context)):
    """会议原声（M4 T6）。

    证据回跳要从"看到片段"走到"听到那句话"，所以必须有这个端点。
    只允许读音频目录下的文件，且路径解析后必须仍在目录内（防目录穿越）。
    样例种子数据没有音频文件，此时返回 404，前端隐藏播放器、只做高亮。
    """
    meeting = MeetingRepo(ctx.conn).get(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="会议不存在")
    if ctx.settings is None:
        raise HTTPException(status_code=500, detail="缺少运行配置")
    try:
        path = audio_path(meeting.audio_file, ctx.settings.audio_dir)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="这场会议没有原声音频") from error
    except PermissionError as error:
        raise HTTPException(status_code=400, detail="音频路径不合法") from error
    return FileResponse(path, media_type="audio/mpeg", filename=path.name)


@router.post("/meetings/{meeting_id}/minutes/regenerate", response_model=MinutesDetail)
def regenerate_minutes(
    meeting_id: str, ctx: ToolContext = Depends(get_context)
) -> MinutesDetail:
    """重新生成纪要（M3 T6）。

    为什么需要这个入口：纪要生成属于编排层，音频任务只在处理完成时跑一次；
    但换了模型、改了提示词、或者想现场演示"同一场会生成两次"时，
    没有这个接口就只能重新上传一遍音频。人工修改存在 override 表里，
    重新生成不会覆盖它们（《技术方案》6.2）。
    """
    meeting = MeetingRepo(ctx.conn).get(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="会议不存在")
    segments = SegmentRepo(ctx.conn).list_for_meeting(meeting_id)
    if not segments:
        raise HTTPException(status_code=409, detail="这场会议还没有转写片段")
    if ctx.settings is None:
        raise HTTPException(status_code=500, detail="缺少运行配置")

    minutes, _notes = generate_meeting_minutes(
        ctx.settings, ctx.conn, meeting, segments, fallback=MinutesRepo(ctx.conn).get(meeting_id)
    )
    MinutesRepo(ctx.conn).save(minutes)
    # M4：重算可能重新产生待确认条目，状态要跟着走（否则会议会留在 processed
    # 却带着一堆待确认项，会议库徽标与列表筛选同时失真）
    refresh_status(ctx.conn, meeting_id, ctx.settings)
    saved = MinutesRepo(ctx.conn).get_with_overrides(meeting_id)
    payload = saved or minutes
    return MinutesDetail(
        meeting_id=meeting.id,
        title=meeting.title,
        date=meeting.date,
        summary=payload.summary,
        decisions=payload.decisions,
        action_items=payload.action_items,
        open_questions=payload.open_questions,
        risks=payload.risks,
    )


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
