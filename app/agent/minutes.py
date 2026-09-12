"""纪要生成（《产品设计文档》5.2 路径 A、《M3 计划》T6）。

三段式：

    segments → map（按预算分段，逐段抽结构）→ reduce（合并成一份）→ Minutes

三个工程要点：

* **分段**：长会议按字符预算切成多个 map 任务，避免一次把整场会议塞进上下文（C5）；
* **校验与重试**：每段都走 `complete_structured`，结构化输出失败会带错误重试，
  最终仍失败就**丢这一段**并在 notes 里记账，而不是让整场会议失败（10.2 第 4 条）；
* **单条降级**：负责人不明、时间缺失、证据缺失的条目置 `needs_review`，
  进待确认区由人处理，而不是直接写进最终稿（约束 C4 + 产品文档 5.1 ④）。

reduce 失败时退化成**本地确定性合并**（`merge_drafts`）：宁可少一次模型调用，
也不能因为合并这一步把已经抽出来的内容丢掉。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.agent.prompts import load_prompt
from app.config import Settings
from app.domain.models import (
    ActionItem,
    Decision,
    Meeting,
    Minutes,
    OpenQuestion,
    REVIEW_CONFIDENCE_THRESHOLD,
    Risk,
    Segment,
    SummaryTopic,
)
from app.llm.client import ChatMessage, LLMClient, LLMUnavailable
from app.llm.structured import complete_structured

CHUNK_CHARS = 2000          # 每个 map 分段的字符预算（与检索层同一套近似口径）


class DraftTopic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str
    points: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class DraftDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.9


class DraftAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str
    owner: str | None = None
    owner_source: Literal["explicit", "inferred", "missing", "unknown"] = "unknown"
    due_text: str | None = None
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.9


class DraftQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str
    evidence: list[str] = Field(default_factory=list)


class DraftRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str
    evidence: list[str] = Field(default_factory=list)


class MinutesDraft(BaseModel):
    """LLM 输出的纪要草稿：**不带 ID 与 meeting_id**。

    ID 由代码统一分配（`t1/d1/a1/...`），不让模型编——否则会出现重复 ID、
    格式漂移，甚至引用一个不存在的条目。
    """

    model_config = ConfigDict(extra="forbid")
    summary: list[DraftTopic] = Field(default_factory=list)
    decisions: list[DraftDecision] = Field(default_factory=list)
    action_items: list[DraftAction] = Field(default_factory=list)
    open_questions: list[DraftQuestion] = Field(default_factory=list)
    risks: list[DraftRisk] = Field(default_factory=list)


def chunk_segments(
    segments: Sequence[Segment], *, max_chars: int = CHUNK_CHARS
) -> list[list[Segment]]:
    """按字符预算分段，同时保证段与段之间不切开同一条片段。"""
    chunks: list[list[Segment]] = []
    current: list[Segment] = []
    used = 0
    for segment in segments:
        cost = len(segment.text) + 24  # 片段头部的说话人与时间戳也要占位
        if current and used + cost > max_chars:
            chunks.append(current)
            current, used = [], 0
        current.append(segment)
        used += cost
    if current:
        chunks.append(current)
    return chunks


def transcript_block(segments: Sequence[Segment], meeting: Meeting) -> str:
    """把片段排成给模型看的转写文本（带片段 ID，便于回填 evidence）。"""
    names = {participant.id: participant.name for participant in meeting.participants}
    lines = []
    for segment in segments:
        speaker = names.get(segment.speaker_id or "", "未识别")
        minutes, seconds = divmod(int(segment.start), 60)
        lines.append("[%s] %02d:%02d %s：%s" % (segment.id, minutes, seconds, speaker, segment.text))
    return "\n".join(lines)


def valid_evidence(segment_ids: set[str], evidence: Sequence[str]) -> list[str]:
    """只保留本次输入里真实存在的片段 ID（约束 C4：引用不能凭空出现）。"""
    return [item for item in evidence if item in segment_ids]


def merge_drafts(drafts: Sequence[MinutesDraft]) -> MinutesDraft:
    """本地确定性合并：reduce 失败时的兜底，保证已抽取的内容不丢。"""
    merged = MinutesDraft()
    topics: dict[str, DraftTopic] = {}
    actions: dict[tuple[str, str], DraftAction] = {}
    decisions: dict[str, DraftDecision] = {}
    for draft in drafts:
        for topic in draft.summary:
            key = topic.topic.strip()
            existing = topics.get(key)
            if existing is None:
                topics[key] = topic.model_copy(deep=True)
                continue
            existing.points.extend(p for p in topic.points if p not in existing.points)
            existing.evidence.extend(e for e in topic.evidence if e not in existing.evidence)
        for item in draft.decisions:
            decisions.setdefault(item.content.strip(), item)
        for item in draft.action_items:
            actions.setdefault((item.task.strip(), (item.owner or "").strip()), item)
        for item in draft.open_questions:
            merged.open_questions.append(item)
        for item in draft.risks:
            merged.risks.append(item)
    merged.summary = list(topics.values())
    merged.decisions = list(decisions.values())
    merged.action_items = list(actions.values())
    return merged


def to_minutes(
    draft: MinutesDraft,
    meeting: Meeting,
    segments: Sequence[Segment],
    *,
    schema_version: int = 1,
) -> tuple[Minutes, list[str]]:
    """草稿 → 领域模型：分配 ID、过滤越界引用、标记待确认条目。"""
    segment_ids = {segment.id for segment in segments}
    names = {participant.name: participant.id for participant in meeting.participants}
    notes: list[str] = []

    summary: list[SummaryTopic] = []
    for index, topic in enumerate(draft.summary, start=1):
        evidence = valid_evidence(segment_ids, topic.evidence)
        if not evidence:
            notes.append("主题「%s」没有有效证据，已标记待确认" % topic.topic)
        summary.append(
            SummaryTopic(topic=topic.topic, points=list(topic.points), evidence=evidence)
        )

    decisions: list[Decision] = []
    for item in draft.decisions:
        evidence = valid_evidence(segment_ids, item.evidence)
        if not evidence:
            notes.append("决议「%s」没有有效证据，已跳过" % item.content[:20])
            continue
        decisions.append(
            Decision(
                # ID 在"保留下来"的条目上连续编号：跳过一条不会留下空号
                id="d%d" % (len(decisions) + 1), content=item.content, evidence=evidence,
                confidence=item.confidence,
                # M4：低置信度决议与低置信度待办同口径——进待确认区，由人拍板
                needs_review=item.confidence < REVIEW_CONFIDENCE_THRESHOLD,
                review_reason=(
                    "置信度 %.2f 低于阈值 %.2f"
                    % (item.confidence, REVIEW_CONFIDENCE_THRESHOLD)
                    if item.confidence < REVIEW_CONFIDENCE_THRESHOLD
                    else None
                ),
            )
        )

    action_items: list[ActionItem] = []
    for item in draft.action_items:
        evidence = valid_evidence(segment_ids, item.evidence)
        owner_id = names.get((item.owner or "").strip()) if item.owner else None
        owner_source = item.owner_source
        if owner_id is None:
            owner_source = "missing" if not item.owner else "unknown"
        reasons: list[str] = []
        if not evidence:
            reasons.append("缺少证据")
        if owner_id is None:
            reasons.append("负责人不明确")
        if not (item.due_text or "").strip():
            reasons.append("截止时间不明确")
        action_items.append(
            ActionItem(
                id="a%d" % (len(action_items) + 1),
                task=item.task,
                owner=owner_id,
                owner_source=owner_source,  # type: ignore[arg-type]
                due_text=(item.due_text or None),
                status="pending",
                evidence=evidence,
                confidence=item.confidence,
                needs_review=bool(reasons),
                review_reason="；".join(reasons) or None,
            )
        )

    open_questions = [
        OpenQuestion(
            id="q%d" % index,
            content=item.content,
            evidence=valid_evidence(segment_ids, item.evidence),
        )
        for index, item in enumerate(draft.open_questions, start=1)
    ]
    risks = [
        Risk(
            id="r%d" % index,
            content=item.content,
            evidence=valid_evidence(segment_ids, item.evidence),
        )
        for index, item in enumerate(draft.risks, start=1)
    ]
    minutes = Minutes(
        meeting_id=meeting.id,
        schema_version=schema_version,
        summary=summary,
        decisions=decisions,
        action_items=action_items,
        open_questions=open_questions,
        risks=risks,
    )
    return minutes, notes


def generate_minutes(
    client: LLMClient | None,
    conn: sqlite3.Connection,
    meeting: Meeting,
    segments: Sequence[Segment],
    *,
    model: str,
    max_retries: int = 2,
    max_chars: int = CHUNK_CHARS,
) -> tuple[Minutes, list[str]]:
    """生成纪要。模型不可用时返回空纪要 + 说明（由调用方决定是否用占位内容）。

    `conn` 目前只作为调用约定保留（音频任务与重新生成接口共用同一入口），
    将来要把分段结果落盘做断点续跑时，签名不用再改。
    """
    if not segments or meeting is None:
        # 防御式收尾：调用方给不到会议或片段时返回结构合法的空纪要，
        # 而不是抛 AttributeError（评测脚本少灌一次语料就踩过这个坑）
        return Minutes(meeting_id=getattr(meeting, "id", "") or ""), [
            "没有片段，跳过纪要生成"
        ]
    if client is None or not getattr(client, "available", False):
        return Minutes(meeting_id=meeting.id), ["模型不可用，纪要待生成"]

    chunks = chunk_segments(segments, max_chars=max_chars)
    drafts: list[MinutesDraft] = []
    notes: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        messages = [
            ChatMessage(role="system", content=load_prompt("minutes_map")),
            ChatMessage(
                role="user",
                content=(
                    "会议信息：%s（%s）\n参会人：%s\n\n转写片段：\n%s"
                    % (
                        meeting.title,
                        meeting.date.isoformat(),
                        "、".join(p.name for p in meeting.participants) or "未标注",
                        transcript_block(chunk, meeting),
                    )
                ),
            ),
        ]
        try:
            outcome = complete_structured(
                client, messages, MinutesDraft, model=model, max_retries=max_retries
            )
        except LLMUnavailable as error:
            notes.append("第 %d/%d 段调用失败：%s" % (index, len(chunks), error))
            continue
        if outcome.value is None:
            notes.append(
                "第 %d/%d 段结构化输出失败（重试 %d 次）：%s"
                % (index, len(chunks), outcome.attempts, "；".join(outcome.errors))
            )
            continue
        drafts.append(outcome.value)  # type: ignore[arg-type]

    if not drafts:
        return Minutes(meeting_id=meeting.id), notes or ["所有分段都没能产出结构化结果"]

    if len(drafts) == 1:
        final = drafts[0]
    else:
        final, note = _reduce(client, drafts, meeting, model=model, max_retries=max_retries)
        if note:
            notes.append(note)

    minutes, conversion_notes = to_minutes(final, meeting, segments)
    return minutes, notes + conversion_notes


def _reduce(
    client: LLMClient,
    drafts: Sequence[MinutesDraft],
    meeting: Meeting,
    *,
    model: str,
    max_retries: int,
) -> tuple[MinutesDraft, str]:
    """合并分段纪要；模型合并失败就退回本地确定性合并。"""
    payload = json.dumps(
        [draft.model_dump(mode="json") for draft in drafts], ensure_ascii=False
    )
    try:
        outcome = complete_structured(
            client,
            [
                ChatMessage(role="system", content=load_prompt("minutes_reduce")),
                ChatMessage(
                    role="user",
                    content="会议：%s\n分段纪要：\n%s" % (meeting.title, payload),
                ),
            ],
            MinutesDraft,
            model=model,
            max_retries=max_retries,
        )
    except LLMUnavailable as error:
        return merge_drafts(drafts), "合并调用失败，已退回本地合并：%s" % error
    if outcome.value is None:
        return merge_drafts(drafts), "合并结构化输出失败，已退回本地合并"
    return outcome.value, ""  # type: ignore[return-value]


def generate_meeting_minutes(
    settings: Settings,
    conn: sqlite3.Connection,
    meeting: Meeting,
    segments: Sequence[Segment],
    *,
    fallback: Minutes | None = None,
) -> tuple[Minutes, list[str]]:
    """音频任务与"重新生成纪要"接口共用的入口：真纪要优先，占位内容兜底。"""
    from app.llm.factory import get_client

    try:
        client = get_client(settings, conn)
    except LLMUnavailable as error:
        return fallback or Minutes(meeting_id=meeting.id), ["模型不可用：%s" % error]

    minutes, notes = generate_minutes(
        client, conn, meeting, segments, model=settings.llm_model_minutes
    )
    has_content = bool(
        minutes.summary or minutes.decisions or minutes.action_items
        or minutes.open_questions or minutes.risks
    )
    if has_content:
        return minutes, notes
    if fallback is not None:
        notes.append("模型没有产出内容，已使用备用纪要")
        return fallback, notes
    return minutes, notes


__all__ = [
    "CHUNK_CHARS",
    "MinutesDraft",
    "chunk_segments",
    "generate_meeting_minutes",
    "generate_minutes",
    "merge_drafts",
    "to_minutes",
    "transcript_block",
    "valid_evidence",
]
