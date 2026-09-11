"""会议库相关工具：list_meetings / get_minutes / update_action_item。"""

from __future__ import annotations

import sqlite3
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import (
    ActionItem,
    Decision,
    Meeting,
    Minutes,
    OpenQuestion,
    Participant,
    Risk,
    SummaryTopic,
    split_item_id,
)
from app.store.minutes_repo import MinutesRepo, OverrideRepo
from app.store.repos import MeetingRepo


class MeetingSummary(BaseModel):
    """会议库列表项：列表页需要的字段 + 计数，不返回片段正文。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    date: date
    time: str | None = None
    duration: float = 0
    status: str
    audio_file: str | None = None
    participants: list[Participant] = Field(default_factory=list)
    segment_count: int = 0
    action_item_count: int = 0
    review_count: int = 0


class ListMeetingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(default=None, description="按标题或参会人模糊匹配")
    date_from: str | None = Field(default=None, description="起始日期，YYYY-MM-DD")
    date_to: str | None = Field(default=None, description="结束日期，YYYY-MM-DD")
    participant: str | None = Field(default=None, description="参会人姓名或 speaker_id")


class ListMeetingsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meetings: list[MeetingSummary] = Field(default_factory=list)


class GetMinutesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meeting_id: str


class MinutesDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meeting_id: str
    title: str
    date: date
    summary: list[SummaryTopic] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)


class GetMinutesOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minutes: MinutesDetail | None = None
    found: bool = False


class UpdateActionItemInput(BaseModel):
    """待办修改：三个字段都可选，只改传进来的那些。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(description="形如 m_20260911:a1")
    status: str | None = Field(default=None, description="pending / done / cancelled")
    owner: str | None = Field(default=None, description="speaker_id")
    due: str | None = Field(default=None, description="YYYY-MM-DD")


class UpdateActionItemOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool = False
    item: ActionItem | None = None
    message: str = ""


def build_summary(conn: sqlite3.Connection, meeting: Meeting) -> MeetingSummary:
    minutes = MinutesRepo(conn).get_with_overrides(meeting.id)
    action_items = minutes.action_items if minutes else []
    return MeetingSummary(
        id=meeting.id,
        title=meeting.title,
        date=meeting.date,
        time=meeting.time,
        duration=meeting.duration,
        status=meeting.status,
        audio_file=meeting.audio_file,
        participants=meeting.participants,
        segment_count=_segment_count(conn, meeting.id),
        action_item_count=len(action_items),
        review_count=sum(1 for item in action_items if item.needs_review),
    )


def _segment_count(conn: sqlite3.Connection, meeting_id: str) -> int:
    from app.store.repos import SegmentRepo

    return SegmentRepo(conn).count(meeting_id)


def list_meetings(conn: sqlite3.Connection, payload: ListMeetingsInput) -> ListMeetingsOutput:
    meetings = MeetingRepo(conn).list(
        q=payload.q,
        date_from=payload.date_from,
        date_to=payload.date_to,
        participant=payload.participant,
    )
    return ListMeetingsOutput(meetings=[build_summary(conn, m) for m in meetings])


def get_minutes(conn: sqlite3.Connection, payload: GetMinutesInput) -> GetMinutesOutput:
    meeting = MeetingRepo(conn).get(payload.meeting_id)
    minutes: Minutes | None = MinutesRepo(conn).get_with_overrides(payload.meeting_id)
    if meeting is None or minutes is None:
        return GetMinutesOutput(found=False)
    return GetMinutesOutput(
        found=True,
        minutes=MinutesDetail(
            meeting_id=meeting.id,
            title=meeting.title,
            date=meeting.date,
            summary=minutes.summary,
            decisions=minutes.decisions,
            action_items=minutes.action_items,
            open_questions=minutes.open_questions,
            risks=minutes.risks,
        ),
    )


def update_action_item(
    conn: sqlite3.Connection, payload: UpdateActionItemInput
) -> UpdateActionItemOutput:
    """修改待办：写 override 表，不直接改纪要 JSON。"""
    meeting_id, item_id = split_item_id(payload.item_id)
    minutes = MinutesRepo(conn).get_with_overrides(meeting_id)
    if minutes is None:
        return UpdateActionItemOutput(found=False, message="会议不存在或还没有纪要")
    if not any(item.id == item_id for item in minutes.action_items):
        return UpdateActionItemOutput(found=False, message="待办条目不存在")

    changes: dict[str, str | None] = {}
    if payload.status is not None:
        changes["status"] = payload.status
    if payload.owner is not None:
        changes["owner"] = payload.owner
    if payload.due is not None:
        changes["due"] = payload.due
    if changes:
        OverrideRepo(conn).upsert(meeting_id, item_id, **changes)
    updated = next(
        item for item in MinutesRepo(conn).get_with_overrides(meeting_id).action_items
        if item.id == item_id
    )
    return UpdateActionItemOutput(found=True, item=updated, message="已更新")


