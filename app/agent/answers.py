"""回答装配（《技术方案》7.3 的 synthesize 节点，M1 为模板版）。

输出 `Draft`（lead + blocks + footnote），由 pipeline 补上意图、范围与引用清单，
组装成对外的 `Answer`。所有引用都指向真实片段，装配阶段拿不到片段就不产生引用，
这是约束 C4「每条结论必须绑定片段 ID」在代码里的第一道保证。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from app.domain.models import (
    ActionItem,
    AnswerBlock,
    AnswerItem,
    Citation,
    Meeting,
    Minutes,
    Segment,
)
from app.store.minutes_repo import MinutesRepo
from app.store.repos import SegmentRepo


def mmss(seconds: float) -> str:
    total = int(seconds)
    return "%02d:%02d" % (total // 60, total % 60)


@dataclass
class Draft:
    lead: str
    blocks: list[AnswerBlock] = field(default_factory=list)
    footnote: str | None = None


@dataclass
class AnswerContext:
    """一次回答需要的全部数据读取入口，避免各构造函数各自查库。"""

    conn: sqlite3.Connection
    meetings: list[Meeting]
    user_input: str = ""

    def __post_init__(self) -> None:
        self._by_id: dict[str, Meeting] = {m.id: m for m in self.meetings}
        self._minutes: dict[str, Minutes | None] = {}
        self._segments: dict[str, list[Segment]] = {}
        self._segments_by_id: dict[tuple[str, str], Segment] = {}

    @property
    def meeting_ids(self) -> list[str]:
        return [m.id for m in self.meetings]

    def meeting(self, meeting_id: str) -> Meeting | None:
        return self._by_id.get(meeting_id)

    def minutes(self, meeting_id: str) -> Minutes | None:
        if meeting_id not in self._minutes:
            self._minutes[meeting_id] = MinutesRepo(self.conn).get_with_overrides(meeting_id)
        return self._minutes[meeting_id]

    def segments(self, meeting_id: str) -> list[Segment]:
        if meeting_id not in self._segments:
            self._segments[meeting_id] = SegmentRepo(self.conn).list_for_meeting(meeting_id)
        return self._segments[meeting_id]

    def segment(self, meeting_id: str, segment_id: str) -> Segment | None:
        key = (meeting_id, segment_id)
        if key not in self._segments_by_id:
            self._segments_by_id[key] = SegmentRepo(self.conn).get(meeting_id, segment_id)
        return self._segments_by_id[key]

    def citation(self, meeting_id: str, segment_id: str) -> Citation | None:
        segment = self.segment(meeting_id, segment_id)
        meeting = self.meeting(meeting_id)
        if segment is None or meeting is None:
            return None
        speaker = meeting.speaker_name(segment.speaker_id)
        return Citation(
            meeting_id=meeting_id,
            meeting_title=meeting.title,
            segment_id=segment_id,
            speaker=speaker,
            start=segment.start,
            label="%s %s" % (speaker, mmss(segment.start)),
        )

    def citations(self, meeting_id: str, segment_ids: list[str]) -> list[Citation]:
        return [
            citation
            for citation in (self.citation(meeting_id, sid) for sid in segment_ids)
            if citation is not None
        ]

    def scope_label(self) -> str:
        if len(self.meetings) == 1:
            return "《%s》" % self.meetings[0].title
        return "全部 %d 场会议" % len(self.meetings)


def quote_block(title: str, items: list[AnswerItem]) -> AnswerBlock:
    return AnswerBlock(kind="quote", title=title, items=items)


def note_block(text: str) -> AnswerBlock:
    return AnswerBlock(kind="note", text=text)


def owner_label(meeting: Meeting | None, item: ActionItem) -> str:
    if not item.owner:
        return "未指定负责人"
    return meeting.speaker_name(item.owner) if meeting else item.owner


def due_label(item: ActionItem) -> str:
    if item.due_text:
        return item.due_text
    if item.due:
        return item.due.isoformat()
    return "未定时间"


def answer_decisions(ctx: AnswerContext) -> Draft:
    items: list[AnswerItem] = []
    total = 0
    for meeting in ctx.meetings:
        minutes = ctx.minutes(meeting.id)
        if minutes is None:
            continue
        for decision in minutes.decisions:
            total += 1
            items.append(
                AnswerItem(
                    title=meeting.title,
                    text=decision.content,
                    citations=ctx.citations(meeting.id, decision.evidence),
                    meta="%d%%" % round(decision.confidence * 100),
                )
            )
    if not items:
        return not_found(ctx)
    return Draft(
        lead="%s一共确认了 %d 项决议，下面是逐条结论与出处。" % (ctx.scope_label(), total),
        blocks=[quote_block("已确认的决议", items)],
    )


def answer_actions(ctx: AnswerContext) -> Draft:
    items: list[AnswerItem] = []
    total = 0
    pending = 0
    for meeting in ctx.meetings:
        minutes = ctx.minutes(meeting.id)
        if minutes is None:
            continue
        for item in minutes.action_items:
            total += 1
            if item.status == "pending":
                pending += 1
            items.append(
                AnswerItem(
                    title="%s · %s" % (owner_label(meeting, item), due_label(item)),
                    text=item.task,
                    citations=ctx.citations(meeting.id, item.evidence),
                    warn=item.needs_review,
                    meta=None if item.status == "pending" else "已完成",
                )
            )
    if not items:
        return not_found(ctx)
    warn = sum(1 for block in items if block.warn)
    return Draft(
        lead="%s共 %d 项待办，其中 %d 项待处理、%d 项因负责人或时间不明确标记为待确认。"
        % (ctx.scope_label(), total, pending, warn),
        blocks=[quote_block("待办清单", items)],
    )


def answer_risks(ctx: AnswerContext) -> Draft:
    items: list[AnswerItem] = []
    for meeting in ctx.meetings:
        minutes = ctx.minutes(meeting.id)
        if minutes is None:
            continue
        for risk in minutes.risks:
            items.append(
                AnswerItem(
                    title=meeting.title,
                    text=risk.content,
                    citations=ctx.citations(meeting.id, risk.evidence),
                )
            )
    if not items:
        return not_found(ctx)
    return Draft(
        lead="%s共记录了 %d 条风险，均来自与会者的原话。" % (ctx.scope_label(), len(items)),
        blocks=[quote_block("风险与预警", items)],
    )


def answer_open_questions(ctx: AnswerContext) -> Draft:
    items: list[AnswerItem] = []
    for meeting in ctx.meetings:
        minutes = ctx.minutes(meeting.id)
        if minutes is None:
            continue
        for question in minutes.open_questions:
            items.append(
                AnswerItem(
                    title=meeting.title,
                    text=question.content,
                    citations=ctx.citations(meeting.id, question.evidence),
                    warn=True,
                )
            )
        for item in minutes.action_items:
            if item.needs_review:
                items.append(
                    AnswerItem(
                        title=meeting.title,
                        text="%s（%s）" % (item.task, item.review_reason or "信息不完整"),
                        citations=ctx.citations(meeting.id, item.evidence),
                        warn=True,
                    )
                )
    if not items:
        return Draft(lead="%s没有找到未决事项。" % ctx.scope_label(), blocks=[])
    return Draft(
        lead="共找到 %d 项尚未闭环的内容，建议在下次会议确认。" % len(items),
        blocks=[quote_block("待确认与未决事项", items)],
    )


def answer_speaker_tasks(ctx: AnswerContext, speaker_id: str, speaker_name: str) -> Draft:
    items: list[AnswerItem] = []
    total = 0
    speaking = 0.0
    for meeting in ctx.meetings:
        minutes = ctx.minutes(meeting.id)
        speaking += sum(
            s.duration for s in ctx.segments(meeting.id) if s.speaker_id == speaker_id
        )
        if minutes is None:
            continue
        for item in minutes.action_items:
            if item.owner != speaker_id:
                continue
            total += 1
            if item.status != "pending":
                continue
            items.append(
                AnswerItem(
                    title="%s · %s" % (meeting.title, due_label(item)),
                    text=item.task,
                    citations=ctx.citations(meeting.id, item.evidence),
                    warn=item.needs_review,
                )
            )
    if not total:
        return Draft(
            lead="在%s中没有找到由%s承担的待办事项。" % (ctx.scope_label(), speaker_name),
            blocks=[],
        )
    return Draft(
        lead="%s在%s中共承担 %d 项待办，累计发言约 %d 分钟。"
        % (speaker_name, ctx.scope_label(), total, round(speaking / 60)),
        blocks=[quote_block("待办明细", items)],
    )


def answer_speaker_speech(ctx: AnswerContext, speaker_id: str, speaker_name: str) -> Draft:
    turns: list[tuple[Meeting, Segment]] = []
    for meeting in ctx.meetings:
        for segment in ctx.segments(meeting.id):
            if segment.speaker_id == speaker_id:
                turns.append((meeting, segment))
    if not turns:
        return not_found(ctx)
    total = sum(segment.duration for _, segment in turns)
    top = sorted(turns, key=lambda pair: pair[1].duration, reverse=True)[:3]
    items = [
        AnswerItem(
            title="%s · %s" % (meeting.title, mmss(segment.start)),
            text=segment.text if len(segment.text) <= 90 else segment.text[:90] + "…",
            citations=ctx.citations(meeting.id, [segment.id]),
        )
        for meeting, segment in top
    ]
    return Draft(
        lead="%s在%s中共发言 %d 次，累计约 %d 分钟。以下是发言最长的三段。"
        % (speaker_name, ctx.scope_label(), len(turns), round(total / 60)),
        blocks=[quote_block("主要发言", items)],
    )


def not_found(ctx: AnswerContext) -> Draft:
    """检索不到时不编造（产品文档 7.3）。"""
    return Draft(
        lead="在%s中没有找到能支撑答案的记录。" % ctx.scope_label(),
        blocks=[
            note_block("我不会基于没有出处的信息作答。可以换个说法再问，或先上传相关会议的录音。")
        ],
    )
