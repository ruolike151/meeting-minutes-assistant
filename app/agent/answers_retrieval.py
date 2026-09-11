"""基于检索片段的回答装配（attribution / content / refuse / clarify）。

与 `answers.py` 里读结构化纪要的构造不同，这里的每一条内容都来自检索层召回的
真实片段，因此「查不到」是常态而不是异常：证据不达标就走拒答。
"""

from __future__ import annotations

from app.agent.answers import AnswerContext, Draft, mmss, note_block, not_found, quote_block
from app.domain.models import AnswerItem, Meeting
from app.retrieval.pipeline import RetrievalResult, RetrievedSegment


def _primary(hits: list[RetrievedSegment], limit: int) -> list[RetrievedSegment]:
    """只要召回片段，不含为语义完整补进来的相邻片段。"""
    ranked = sorted(
        (hit for hit in hits if not hit.is_neighbor), key=lambda hit: hit.score, reverse=True
    )
    return ranked[:limit]


def speaker_role(meeting: Meeting | None, speaker_id: str | None) -> str:
    if meeting is None or not speaker_id:
        return ""
    for participant in meeting.participants:
        if participant.id == speaker_id:
            return participant.role or ""
    return ""


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _title_of(ctx: AnswerContext, meeting_id: str) -> str:
    meeting = ctx.meeting(meeting_id)
    return meeting.title if meeting else meeting_id


def _speaker_of(ctx: AnswerContext, meeting_id: str, speaker_id: str | None) -> str:
    meeting = ctx.meeting(meeting_id)
    return meeting.speaker_name(speaker_id) if meeting else "未识别"


def answer_attribution(ctx: AnswerContext, retrieval: RetrievalResult) -> Draft:
    """「谁提到了……？」——定位到具体发言人与时间点。"""
    hits = _primary(retrieval.hits, 3)
    if retrieval.refused or not hits:
        return not_found(ctx)

    top = hits[0]
    meeting = ctx.meeting(top.segment.meeting_id)
    speaker = _speaker_of(ctx, top.segment.meeting_id, top.segment.speaker_id)
    role = speaker_role(meeting, top.segment.speaker_id)

    items = [
        AnswerItem(
            title="%s · %s %s"
            % (
                _title_of(ctx, hit.segment.meeting_id),
                _speaker_of(ctx, hit.segment.meeting_id, hit.segment.speaker_id),
                mmss(hit.segment.start),
            ),
            text=_clip(hit.segment.text, 110),
            citations=ctx.citations(hit.segment.meeting_id, [hit.segment.id]),
        )
        for hit in hits
    ]
    others = [hit for hit in hits[1:] if hit.segment.speaker_id != top.segment.speaker_id]
    lead = "这个问题主要是 %s%s 在《%s》%s 提到的。" % (
        speaker,
        "（%s）" % role if role else "",
        _title_of(ctx, top.segment.meeting_id),
        mmss(top.segment.start),
    )
    footnote = "另有 %d 处相关发言，可点击引用核对。" % len(others) if others else None
    return Draft(lead=lead, blocks=[quote_block("相关原话", items)], footnote=footnote)


def answer_content(ctx: AnswerContext, retrieval: RetrievalResult) -> Draft:
    """开放内容检索：按会议分组给出最相关片段。"""
    hits = _primary(retrieval.hits, 4)
    if retrieval.refused or not hits:
        return not_found(ctx)

    grouped: dict[str, list[RetrievedSegment]] = {}
    for hit in hits:
        grouped.setdefault(hit.segment.meeting_id, []).append(hit)

    items: list[AnswerItem] = []
    for meeting_id, group in grouped.items():
        best = group[0]
        items.append(
            AnswerItem(
                title="%s · %s %s"
                % (
                    _title_of(ctx, meeting_id),
                    _speaker_of(ctx, meeting_id, best.segment.speaker_id),
                    mmss(best.segment.start),
                ),
                text=_clip(best.segment.text, 120),
                citations=ctx.citations(meeting_id, [hit.segment.id for hit in group]),
            )
        )
    return Draft(
        lead="在%s中找到 %d 处相关内容，最相关的是下面几条。" % (ctx.scope_label(), len(hits)),
        blocks=[quote_block("相关片段", items)],
    )


def answer_refuse(ctx: AnswerContext, retrieval: RetrievalResult) -> Draft:
    """拒答：讲清检索判定，但不给未经证据支持的结论。"""
    draft = not_found(ctx)
    draft.blocks.append(
        note_block("本次检索的判定：%s。" % (retrieval.reason or "命中片段不足以支撑回答"))
    )
    return draft


def answer_clarify(candidates: list[Meeting]) -> Draft:
    """范围不唯一时反问，不做猜测（产品文档 5.2 澄清追问）。"""
    items = [
        AnswerItem(
            title="%s · %s" % (meeting.title, meeting.date.isoformat()),
            text="、".join(p.name for p in meeting.participants),
        )
        for meeting in candidates
    ]
    return Draft(
        lead="你指的是下面哪一场会议？确认之后我再具体回答。",
        blocks=[quote_block("可选会议", items)],
    )
