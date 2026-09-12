"""检索 / 判定 / 拒答节点（《技术方案》7.2 的 retrieve、grade、refuse）。"""

from __future__ import annotations

import time

from app.agent import answers_retrieval
from app.agent.answers import AnswerContext
from app.agent.nodes.context import NodeContext, append_trace, scope_meetings
from app.agent.state import AgentState
from app.retrieval.pipeline import RetrievalResult, retrieve
from app.retrieval.timefilter import parse_time_range


def retrieve_node(state: AgentState, context: NodeContext) -> dict:
    """混合检索：过滤 → BM25 → 向量 → RRF → 阈值 → 相邻片段组装。"""
    started = time.perf_counter()
    user_input = state.get("user_input") or ""
    meetings = scope_meetings(state, context)
    time_range = parse_time_range(user_input)
    date_from, date_to = time_range if time_range else (None, None)

    result = retrieve(
        context.conn,
        user_input,
        meeting_ids=[meeting.id for meeting in meetings] or None,
        date_from=date_from,
        date_to=date_to,
        encoder=context.encoder,
        config=context.retrieval_settings,
    )
    detail = "命中 %d 条（关键词 %d / 向量 %d）" % (
        len(result.hits),
        result.keyword_hits,
        result.vector_hits,
    )
    if result.is_hybrid:
        detail += "｜编码器 %s" % result.encoder
    if context.encoder_reason:
        detail += "｜%s" % context.encoder_reason
    trace = append_trace(state, "retrieve", started, detail)
    return {
        "retrieval": result,
        "evidence": [hit.segment for hit in result.hits],
        "encoder": result.encoder,
        "encoder_reason": context.encoder_reason,
        "trace": trace,
    }


def grade_node(state: AgentState, context: NodeContext) -> dict:
    """判断证据是否足以回答。**这一步不调用模型**：阈值判定必须确定。"""
    started = time.perf_counter()
    result = state.get("retrieval")
    enough = bool(result is not None and not result.refused)
    reason = "" if enough else (getattr(result, "reason", "") or "证据不足")
    trace = append_trace(state, "grade", started, "通过" if enough else reason)
    return {"enough_evidence": enough, "grade_reason": reason, "trace": trace}


def refuse_node(state: AgentState, context: NodeContext) -> dict:
    """拒答：讲清检索判定，但不给任何没有出处的结论（同样不调用模型）。"""
    started = time.perf_counter()
    answer_ctx = AnswerContext(
        conn=context.conn, meetings=scope_meetings(state, context), user_input=state.get("user_input", "")
    )
    retrieval = state.get("retrieval") or RetrievalResult(
        refused=True, reason="证据不足，没有找到可以支撑回答的记录"
    )
    draft = answers_retrieval.answer_refuse(answer_ctx, retrieval)
    trace = append_trace(state, "refuse", started, state.get("grade_reason") or "")
    return {
        "answer_draft": draft,
        "answer_mode": "refuse",
        "synthesis_source": "template",
        "trace": trace,
    }


__all__ = ["grade_node", "refuse_node", "retrieve_node"]
