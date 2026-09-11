"""检索与发言人工具：search_segments / query_speaker / flag_uncertainty。"""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import ActionItem, split_item_id
from app.retrieval.pipeline import RetrievedSegment, retrieve
from app.store.minutes_repo import MinutesRepo, OverrideRepo
from app.store.repos import MeetingRepo, SegmentRepo


class SegmentHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meeting_id: str
    meeting_title: str
    segment_id: str
    start: float
    end: float
    speaker_id: str | None = None
    speaker_name: str
    text: str
    score: float = 0
    is_neighbor: bool = False


class SearchSegmentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    meeting_ids: list[str] = Field(default_factory=list)
    speaker_ids: list[str] = Field(default_factory=list)
    top_k: int | None = None


class SearchSegmentsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hits: list[SegmentHit] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)
    missing_terms: list[str] = Field(default_factory=list)
    coverage: float = 0
    refused: bool = True
    reason: str = ""


class QuerySpeakerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: str = Field(description="参会人姓名或 speaker_id")
    meeting_ids: list[str] = Field(default_factory=list)
    top_segments: int = Field(default=3, ge=0, le=20)


class QuerySpeakerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool = False
    speaker_id: str | None = None
    speaker_name: str | None = None
    meeting_ids: list[str] = Field(default_factory=list)
    turn_count: int = 0
    speaking_seconds: float = 0
    action_items: list[ActionItem] = Field(default_factory=list)
    segments: list[SegmentHit] = Field(default_factory=list)


class FlagUncertaintyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(description="形如 m_20260911:a1")
    reason: str = Field(default="", description="为什么需要人工确认")


class FlagUncertaintyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool = False
    item_id: str
    message: str = ""


def _to_hit(meeting_repo: MeetingRepo, item: RetrievedSegment) -> SegmentHit:
    meeting = meeting_repo.get(item.segment.meeting_id)
    return SegmentHit(
        meeting_id=item.segment.meeting_id,
        meeting_title=meeting.title if meeting else item.segment.meeting_id,
        segment_id=item.segment.id,
        start=item.segment.start,
        end=item.segment.end,
        speaker_id=item.segment.speaker_id,
        speaker_name=meeting.speaker_name(item.segment.speaker_id) if meeting else "未识别",
        text=item.segment.text,
        score=item.score,
        is_neighbor=item.is_neighbor,
    )


def search_segments(conn: sqlite3.Connection, payload: SearchSegmentsInput) -> SearchSegmentsOutput:
    """片段级检索：过滤 -> 关键词召回 -> 阈值拒答 -> 相邻片段组装。

    返回顺序是「按相关度排名的命中片段」在前、「为语义完整补进来的相邻片段」在后。
    需要按时间顺序拼上下文的调用方（合成节点）自己再排一次，
    工具的输出保持「检索结果」该有的语义。
    """
    kwargs = {}
    if payload.top_k:
        kwargs["top_k"] = payload.top_k
    result = retrieve(
        conn,
        payload.query,
        meeting_ids=payload.meeting_ids or None,
        speaker_ids=payload.speaker_ids or None,
        **kwargs,
    )
    meeting_repo = MeetingRepo(conn)
    ordered = sorted(result.hits, key=lambda item: (item.is_neighbor, -item.score))
    return SearchSegmentsOutput(
        hits=[_to_hit(meeting_repo, item) for item in ordered],
        matched_terms=result.matched_terms,
        missing_terms=result.missing_terms,
        coverage=result.coverage,
        refused=result.refused,
        reason=result.reason,
    )


def query_speaker(conn: sqlite3.Connection, payload: QuerySpeakerInput) -> QuerySpeakerOutput:
    """发言人维度汇总：发言时长、发言片段、名下待办（产品文档 5.2）。"""
    meeting_repo = MeetingRepo(conn)
    meetings = [
        meeting
        for meeting in meeting_repo.list()
        if not payload.meeting_ids or meeting.id in set(payload.meeting_ids)
    ]
    speaker_id: str | None = None
    speaker_name: str | None = None
    for meeting in meetings:
        for participant in meeting.participants:
            if payload.speaker in (participant.id, participant.name):
                speaker_id, speaker_name = participant.id, participant.name
                break
        if speaker_id:
            break
    if speaker_id is None:
        return QuerySpeakerOutput(found=False)

    segment_repo = SegmentRepo(conn)
    minutes_repo = MinutesRepo(conn)
    hits: list[SegmentHit] = []
    turn_count = 0
    speaking = 0.0
    scoped_meeting_ids: list[str] = []
    action_items: list[ActionItem] = []
    for meeting in meetings:
        turns = [
            segment
            for segment in segment_repo.list_for_meeting(meeting.id)
            if segment.speaker_id == speaker_id
        ]
        if not turns:
            continue
        scoped_meeting_ids.append(meeting.id)
        turn_count += len(turns)
        speaking += sum(turn.duration for turn in turns)
        for item in minutes_repo.action_items(meeting.id):
            if item.owner == speaker_id:
                action_items.append(item)
        for turn in sorted(turns, key=lambda s: s.duration, reverse=True)[: payload.top_segments]:
            hits.append(
                SegmentHit(
                    meeting_id=meeting.id,
                    meeting_title=meeting.title,
                    segment_id=turn.id,
                    start=turn.start,
                    end=turn.end,
                    speaker_id=turn.speaker_id,
                    speaker_name=speaker_name or "未识别",
                    text=turn.text,
                )
            )
    return QuerySpeakerOutput(
        found=True,
        speaker_id=speaker_id,
        speaker_name=speaker_name,
        meeting_ids=scoped_meeting_ids,
        turn_count=turn_count,
        speaking_seconds=round(speaking, 1),
        action_items=action_items,
        segments=hits,
    )


def flag_uncertainty(
    conn: sqlite3.Connection, payload: FlagUncertaintyInput
) -> FlagUncertaintyOutput:
    """把存疑条目录入待确认区：写 override，不改模型生成的纪要。"""
    meeting_id, item_id = split_item_id(payload.item_id)
    minutes = MinutesRepo(conn).get_with_overrides(meeting_id)
    if minutes is None or not any(item.id == item_id for item in minutes.action_items):
        return FlagUncertaintyOutput(
            found=False, item_id=payload.item_id, message="待办条目不存在"
        )
    OverrideRepo(conn).upsert(
        meeting_id,
        item_id,
        resolution="pending",
        note=payload.reason or "标记为待确认",
    )
    return FlagUncertaintyOutput(found=True, item_id=payload.item_id, message="已录入待确认区")
