"""合成 / 证据校验 / 落库节点（《技术方案》7.2 的 synthesize、verify、finalize）。

合成节点分两条路：

* **结构化问题**（决议 / 待办 / 风险 / 未决 / 发言人维度）读的是已经结构化的纪要，
  用确定性模板装配——这些内容本来就该由"数据"决定，让模型参与只会引入不确定性；
* **内容与归属问题**（"谁提到了……""为什么……"）必须先检索片段，
  再由模型基于证据合成；模型不可用时降级成 M1 的模板装配，回答依旧带引用。
"""

from __future__ import annotations

import time

from app.agent import answers, answers_retrieval
from app.agent.answers import AnswerContext, Draft, not_found
from app.agent.nodes.context import NodeContext, append_trace, scope_meetings
from app.agent.persist import ChatResult, flatten_citations, persist_answer, plain_text
from app.agent.state import AgentState
from app.agent.synthesize import synthesize_with_llm
from app.agent.verify import verify_evidence
from app.domain.models import Answer
from app.store.traces_repo import TraceRepo

# 这些形态读结构化纪要即可，不需要模型
STRUCTURED_MODES = (
    "decisions",
    "actions",
    "risks",
    "open_questions",
    "speaker_tasks",
    "speaker_speech",
)


def synthesize_node(state: AgentState, context: NodeContext) -> dict:
    started = time.perf_counter()
    answer_ctx = _answer_context(state, context)
    mode = state.get("answer_mode") or "content"
    source = "template"
    note = "模板装配"
    draft: Draft | None = None
    dropped = 0

    if mode in ("content", "attribution") and context.llm_available():
        draft, note, dropped = synthesize_with_llm(state, context, answer_ctx)
        if draft is not None:
            source = "llm"

    if draft is None:
        draft = _template_draft(state, answer_ctx, mode)
        if source == "llm":  # 上面拿到了说明但草稿为空不会有这种组合，保险起见
            source = "template"

    trace = append_trace(state, "synthesize", started, "%s（%s）" % (note, source))
    return {
        "answer_draft": draft,
        "synthesis_source": source,
        "dropped_citations": dropped,
        "trace": trace,
    }


def _answer_context(state: AgentState, context: NodeContext) -> AnswerContext:
    return AnswerContext(
        conn=context.conn,
        meetings=scope_meetings(state, context),
        user_input=state.get("user_input") or "",
    )


def _template_draft(state: AgentState, answer_ctx: AnswerContext, mode: str) -> Draft:
    """确定性装配：结构化问题走纪要，内容问题走检索片段模板。"""
    if mode == "speaker_tasks" and state.get("speaker_id"):
        draft = answers.answer_speaker_tasks(
            answer_ctx, state["speaker_id"], state.get("speaker_name") or "该成员"
        )
        return draft if draft.blocks else not_found(answer_ctx)
    if mode == "speaker_speech" and state.get("speaker_id"):
        draft = answers.answer_speaker_speech(
            answer_ctx, state["speaker_id"], state.get("speaker_name") or "该成员"
        )
        return draft if draft.blocks else not_found(answer_ctx)
    if mode == "decisions":
        return answers.answer_decisions(answer_ctx)
    if mode == "actions":
        return answers.answer_actions(answer_ctx)
    if mode == "risks":
        return answers.answer_risks(answer_ctx)
    if mode == "open_questions":
        return answers.answer_open_questions(answer_ctx)

    retrieval = state.get("retrieval")
    if retrieval is None or retrieval.refused:
        return answers_retrieval.answer_refuse(answer_ctx, retrieval)
    if mode == "attribution":
        return answers_retrieval.answer_attribution(answer_ctx, retrieval)
    return answers_retrieval.answer_content(answer_ctx, retrieval)


def verify_evidence_node(state: AgentState, context: NodeContext) -> dict:
    """证据校验：丢弃解析不到的引用，无证据结论标 warn（约束 C4）。"""
    started = time.perf_counter()
    answer_ctx = _answer_context(state, context)
    draft = state.get("answer_draft") or not_found(answer_ctx)
    verified, dropped, unsupported = verify_evidence(answer_ctx, draft)
    detail = "丢弃无效引用 %d / 无证据结论 %d" % (dropped, unsupported)

    if context.verify_mode == "llm" and context.llm_available():
        verified, judged = _llm_verify(verified, state, context)
        unsupported += judged
        detail += "；模型判定不支撑 %d 条" % judged

    trace = append_trace(state, "verify_evidence", started, detail)
    return {
        "answer_draft": verified,
        "dropped_citations": dropped,
        "unsupported_items": unsupported,
        "verify_detail": detail,
        "trace": trace,
    }


def _llm_verify(draft: Draft, state: AgentState, context: NodeContext) -> tuple[Draft, int]:
    """可选的一条：让模型判断"片段是否真的支撑结论"（`MA_VERIFY_MODE=llm`）。

    一次调用判定全部结论（不是每条一次），成本随结论数线性、随调用数不变。
    失败时保持原样——校验是增强，不该成为新的失败点。
    """
    from pydantic import BaseModel, ConfigDict, Field

    from app.agent.prompts import load_prompt
    from app.llm.client import ChatMessage, LLMUnavailable
    from app.llm.structured import complete_structured

    class _Verdict(BaseModel):
        model_config = ConfigDict(extra="forbid")
        index: int
        supported: bool
        reason: str = ""

    class _Verdicts(BaseModel):
        model_config = ConfigDict(extra="forbid")
        results: list[_Verdict] = Field(default_factory=list)

    pairs = [
        (block_index, item_index, item)
        for block_index, block in enumerate(draft.blocks)
        if block.kind != "note"
        for item_index, item in enumerate(block.items)
    ]
    if not pairs:
        return draft, 0

    lines = [load_prompt("verify"), "", "待判定的结论："]
    for index, (_, _, item) in enumerate(pairs):
        lines.append(
            "%d. 结论：%s｜引用原文：%s"
            % (
                index,
                item.text or "",
                " / ".join(citation.label for citation in item.citations) or "（无引用）",
            )
        )
    try:
        outcome = complete_structured(
            context.llm,
            [ChatMessage(role="user", content="\n".join(lines))],
            _Verdicts,
            model=context.model_answer or "default",
            max_retries=1,
        )
    except LLMUnavailable:
        return draft, 0
    if outcome.value is None:
        return draft, 0

    rejected = {item.index for item in outcome.value.results if not item.supported}  # type: ignore[attr-defined]
    if not rejected:
        return draft, 0
    blocks = []
    for block_index, block in enumerate(draft.blocks):
        if block.kind == "note":
            blocks.append(block)
            continue
        items = []
        for item_index, item in enumerate(block.items):
            if (block_index, item_index) in rejected:
                item = item.model_copy(update={"warn": True})
            items.append(item)
        blocks.append(block.model_copy(update={"items": items}))
    return Draft(lead=draft.lead, blocks=blocks, footnote=draft.footnote), len(rejected)


def finalize_node(state: AgentState, context: NodeContext) -> dict:
    """组装 Answer、落库会话、写 chat_traces（成本与节点耗时在这里入库）。"""
    started = time.perf_counter()
    answer_ctx = _answer_context(state, context)
    draft = state.get("answer_draft") or not_found(answer_ctx)
    citations = flatten_citations(draft)
    meetings = scope_meetings(state, context)
    answer = Answer(
        lead=draft.lead,
        blocks=draft.blocks,
        footnote=draft.footnote,
        intent=state.get("intent") or "unknown",
        answer_mode=state.get("answer_mode") or "content",
        scope_meeting_ids=[meeting.id for meeting in meetings],
        citations=citations,
    )
    result = persist_answer(
        context.conn, state.get("user_input") or "", answer, state.get("conversation_id")
    )
    trace = append_trace(state, "persist", started, result.conversation_id)
    answer = answer.model_copy(update={"trace": trace})
    result.answer = answer
    result.trace = trace
    result.assistant_message = result.assistant_message.model_copy(update={"answer": answer})
    # trace 里含 persist 这一步，补写一次会话记录（M1 的同一做法）
    from app.store.conversations_repo import ConversationRepo

    ConversationRepo(context.conn).append(result.conversation_id, result.assistant_message)

    usage = context.usage.to_dict() if context.usage is not None else {}
    # M5：把延迟与会议关联写进 trace，评测才能算 P95 与单场成本。
    # 延迟取各节点耗时之和：它包含检索、模型与落库，是"用户等了多久"的近似。
    latency_ms = int(sum(step.ms for step in trace))
    # 范围唯一时把会议记到 trace 上；跨会议问答没有单一归属，记为 None
    meeting_ids = [meeting.id for meeting in meetings]
    trace_meeting = meeting_ids[0] if len(meeting_ids) == 1 else None
    cached = bool(usage.get("calls")) and usage.get("cache_hits") == usage.get("calls")
    TraceRepo(context.conn).save(
        conversation_id=result.conversation_id,
        intent=answer.intent,
        answer_mode=answer.answer_mode,
        route_source=state.get("route_source") or "",
        nodes=trace,
        model=",".join(usage.get("models") or []) or None,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cost=float(usage.get("cost") or 0.0),
        latency_ms=latency_ms,
        meeting_id=trace_meeting,
        cached=cached,
    )
    return {
        "answer": answer,
        "citations": [citation.key for citation in citations],
        "result": result,
        "usage": usage,
        "trace": trace,
    }


__all__ = [
    "ChatResult",
    "STRUCTURED_MODES",
    "finalize_node",
    "plain_text",
    "synthesize_node",
    "verify_evidence_node",
]
