"""基于证据的回答合成（《M3 计划》5.8）。

模型输出的是**结构化草稿**，不是自由文本：每条结论自带 `evidence` 片段 ID，
引用关系因此在结构上无法伪造。转换时还会做一次白名单过滤——
**只接受本次检索命中的片段 ID**，模型引用别处的片段一律丢弃并记账。

这比"让模型在回答里插 `[1]` 标记、再正则解析"可靠得多：
引用要么是一个真实存在的 ID，要么根本不进回答。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.agent.answers import AnswerContext, Draft, quote_block
from app.agent.nodes.context import NodeContext
from app.agent.prompts import load_prompt
from app.agent.state import AgentState
from app.domain.models import AnswerItem
from app.llm.client import ChatMessage, LLMUnavailable
from app.llm.structured import complete_structured


class AnswerDraftItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    text: str
    evidence: list[str] = Field(default_factory=list, description="形如 m_20260911:s3")


class AnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead: str
    items: list[AnswerDraftItem] = Field(default_factory=list)
    footnote: str | None = None


def evidence_keys(state: AgentState) -> set[str]:
    """本次检索允许被引用的片段 ID（白名单）。"""
    retrieval = state.get("retrieval")
    if retrieval is None:
        return set()
    return {"%s:%s" % (hit.segment.meeting_id, hit.segment.id) for hit in retrieval.hits}


def evidence_block(state: AgentState, context: NodeContext) -> str:
    """把命中片段排成给模型看的证据清单（带可引用的 ID、时间、发言人）。"""
    retrieval = state.get("retrieval")
    if retrieval is None:
        return ""
    meetings = {meeting.id: meeting for meeting in context.library()}
    lines: list[str] = ["证据片段（引用时必须用方括号里的 ID）："]
    for hit in sorted(
        retrieval.hits, key=lambda item: (item.segment.meeting_id, item.segment.start)
    ):
        meeting = meetings.get(hit.segment.meeting_id)
        speaker = (
            meeting.speaker_name(hit.segment.speaker_id) if meeting else "未识别"
        )
        title = meeting.title if meeting else hit.segment.meeting_id
        minutes, seconds = divmod(int(hit.segment.start), 60)
        lines.append(
            "[%s:%s] 《%s》%02d:%02d %s：%s"
            % (
                hit.segment.meeting_id,
                hit.segment.id,
                title,
                minutes,
                seconds,
                speaker,
                hit.segment.text,
            )
        )
    return "\n".join(lines)


def synthesize_with_llm(
    state: AgentState, context: NodeContext, answer_ctx: AnswerContext
) -> tuple[Draft | None, str, int]:
    """调用模型生成草稿，返回 (草稿 | None, 说明, 被丢弃的引用数)。

    模型不可用、调用失败或结构化输出三次都不合法时返回 None——
    由调用方降级到模板装配，用户看到的仍然是"有证据的回答"。
    """
    user_input = state.get("user_input") or ""
    messages = [ChatMessage(role="system", content=load_prompt("synthesize"))]
    history = context.history_payload()
    if history:
        messages.append(ChatMessage(role="system", content="最近对话：\n%s" % history))
    messages.append(ChatMessage(role="system", content=evidence_block(state, context)))
    messages.append(ChatMessage(role="user", content=user_input))

    try:
        outcome = complete_structured(
            context.llm,
            messages,
            AnswerDraft,
            model=context.model_answer or "default",
            temperature=0.0,
        )
    except LLMUnavailable as error:
        return None, "模型不可用，已降级为模板装配：%s" % error, 0
    if outcome.value is None:
        return None, "结构化输出失败，已降级为模板装配：%s" % "; ".join(outcome.errors), 0

    payload: AnswerDraft = outcome.value  # type: ignore[assignment]
    allowed = evidence_keys(state)
    items: list[AnswerItem] = []
    dropped = 0
    for item in payload.items:
        kept_keys = [key for key in item.evidence if key in allowed]
        dropped += len(item.evidence) - len(kept_keys)
        grouped: dict[str, list[str]] = {}
        for key in kept_keys:
            meeting_id, _, segment_id = key.partition(":")
            grouped.setdefault(meeting_id, []).append(segment_id)
        citations = []
        for meeting_id, segment_ids in grouped.items():
            citations.extend(answer_ctx.citations(meeting_id, segment_ids))
        items.append(
            AnswerItem(
                title=item.title,
                text=item.text,
                citations=citations,
                # 模型给了结论却没给出可用的证据 ID：标记待确认，
                # 不让"无法核对的结论"混进最终稿（约束 C4）
                warn=not citations,
            )
        )
    draft = Draft(
        lead=payload.lead,
        blocks=[quote_block("相关结论", items)] if items else [],
        footnote=payload.footnote,
    )
    note = "模型合成，引用 %d 条" % sum(len(item.citations) for item in items)
    if dropped:
        note += "；丢弃越界引用 %d 条" % dropped
    return draft, note, dropped


__all__ = [
    "AnswerDraft",
    "AnswerDraftItem",
    "evidence_block",
    "evidence_keys",
    "synthesize_with_llm",
]
