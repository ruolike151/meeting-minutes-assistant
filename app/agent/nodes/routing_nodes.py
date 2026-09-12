"""路由 / 范围 / 澄清 / 建任务节点（《技术方案》7.2、7.3）。

路由策略是**模型优先、规则兜底**（`MA_ROUTE_MODE=auto`）：

* 模型给出结构化结论，但**必须经过三道护栏**才被采纳：
  会议 ID 必须真实存在、发言人必须能在库里找到、`intent` 必须落在枚举内；
* 任何一道没过，或者模型判成 `unknown`，就回落到 M1 的规则表——
  规则表覆盖不了才落到内容检索，因此"路由结果"永远是确定的枚举，
  不会出现"模型说了算但说不清"的状态（约束 C1）。

这么做还有一层工程意义：**关掉模型（MA_LLM_MODE=off）时行为完全可复现**，
面试现场可以直接对比"模型路由"与"规则路由"两种模式的结果。
"""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, Field

from app.agent import routing
from app.agent.history import inherit_scope
from app.agent.nodes.context import NodeContext, append_trace
from app.agent.prompts import load_prompt
from app.agent.state import AgentState
from app.domain.models import AnswerMode, Intent
from app.llm.client import ChatMessage, LLMUnavailable
from app.llm.structured import complete_structured


class RouteDecision(BaseModel):
    """模型输出的路由结论（schema 即护栏：字段不全就校验失败，走规则兜底）。"""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    answer_mode: AnswerMode
    meeting_ids: list[str] = Field(default_factory=list)
    speaker_id: str | None = None
    reason: str = ""


def route_node(state: AgentState, context: NodeContext) -> dict:
    started = time.perf_counter()
    user_input = state.get("user_input") or ""
    meetings = context.library()
    rules = routing.route(
        user_input, meetings=meetings, active_meeting_id=state.get("active_meeting_id")
    )

    decision = rules
    source = "rules"
    rejected: list[str] = []
    mode = context.route_mode or "auto"

    if mode in ("auto", "llm") and context.llm_available():
        try:
            llm_decision = _classify_with_llm(context, user_input, meetings)
        except LLMUnavailable as error:
            rejected.append("模型不可用：%s" % error)
        else:
            llm_decision, notes = _apply_guardrails(llm_decision, meetings, rules)
            rejected.extend(notes)
            if llm_decision is not None:
                decision = llm_decision
                source = "llm"
            elif mode == "llm":
                rejected.append("已回落到规则路由")

    scope = apply_history_scope(
        _scope_payload(decision, rules, state), context, state.get("active_meeting_id")
    )
    speaker_name = decision.speaker_name
    trace = append_trace(
        state, "route", started, "%s / %s（%s）" % (decision.intent, decision.answer_mode, source)
    )
    return {
        "intent": decision.intent,
        "answer_mode": decision.answer_mode,
        "scope": scope,
        "speaker_id": decision.speaker_id,
        "speaker_name": speaker_name,
        "route_source": source,
        "route_reason": decision.reason,
        "route_rejected": rejected,
        "trace": trace,
    }


def _classify_with_llm(context: NodeContext, user_input: str, meetings) -> RouteDecision:
    """让模型做一次结构化路由（schema 校验失败会自带错误重试）。"""
    system = load_prompt("route") + "\n\n" + _candidates(meetings, context)
    outcome = complete_structured(
        context.llm,
        [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user_input)],
        RouteDecision,
        model=context.model_route or "default",
        temperature=0.0,
        max_retries=1,
    )
    if outcome.value is None:
        raise LLMUnavailable("路由结构化输出失败：%s" % "；".join(outcome.errors))
    return outcome.value  # type: ignore[return-value]


def _candidates(meetings, context: NodeContext) -> str:
    """给模型的候选清单：只列真实存在的会议与发言人，防止它凭空造 ID。"""
    lines = ["候选会议清单："]
    for meeting in meetings:
        line = "- id=%s 标题=%s 日期=%s" % (meeting.id, meeting.title, meeting.date.isoformat())
        for participant in meeting.participants:
            line += " / 发言人 id=%s 姓名=%s" % (participant.id, participant.name)
        lines.append(line)
    history = context.history_payload()
    if history:
        lines.append("")
        lines.append("最近对话：")
        lines.append(history)
    return "\n".join(lines)


def _apply_guardrails(
    decision: RouteDecision, meetings, rules: routing.RouteResult
) -> tuple[routing.RouteResult | None, list[str]]:
    """三道护栏：会议 ID 真实、发言人存在、intent 不是 unknown。

    返回 (可用结论 | None, 被丢弃的内容说明)。
    """
    notes: list[str] = []
    if decision.intent == "unknown":
        notes.append("模型判为 unknown")
        return None, notes

    valid_ids = {meeting.id for meeting in meetings}
    kept_ids = [item for item in decision.meeting_ids if item in valid_ids]
    dropped = [item for item in decision.meeting_ids if item not in valid_ids]
    if dropped:
        notes.append("丢弃不存在的会议 ID：%s" % "、".join(dropped))

    speaker_id, speaker_name = _resolve_speaker(decision.speaker_id, meetings)
    if decision.answer_mode in ("speaker_tasks", "speaker_speech") and not speaker_id:
        notes.append("模型说是发言人问题但没有给出有效发言人")
        return None, notes
    if decision.intent == "query_speaker" and not speaker_id:
        notes.append("模型判为发言人查询但发言人无效")
        return None, notes

    if kept_ids:
        scope = routing.Scope(meeting_ids=kept_ids, narrowed=True, ambiguous=len(kept_ids) > 1,
                              source="explicit")
    else:
        scope = rules.scope
    return (
        routing.RouteResult(
            intent=decision.intent,
            answer_mode=decision.answer_mode,
            scope=scope,
            speaker_id=speaker_id,
            speaker_name=speaker_name,
            reason=decision.reason or "llm",
        ),
        notes,
    )


def _resolve_speaker(speaker_id: str | None, meetings) -> tuple[str | None, str | None]:
    if not speaker_id:
        return None, None
    for meeting in meetings:
        for participant in meeting.participants:
            if participant.id == speaker_id or participant.name == speaker_id:
                return participant.id, participant.name
    return None, None


def _scope_payload(decision, rules: routing.RouteResult, state: AgentState) -> dict:
    """把 dataclass 形式的 Scope 转成状态里的 dict，并保留来源说明。"""
    scope = decision.scope if decision is not rules else rules.scope
    return {
        "meeting_ids": list(scope.meeting_ids),
        "narrowed": bool(scope.narrowed),
        "ambiguous": bool(scope.ambiguous),
        "source": scope.source,
    }


def apply_history_scope(
    scope: dict, context: NodeContext, active_meeting_id: str | None
) -> dict:
    """范围继承：本轮没点名、界面也没锁定会议时，沿用上一轮的范围。

    「我说的是哪场会」只该说一次——上一轮已经确认过范围，
    这一轮说"那这场会还有什么风险"就不该退回整个会议库。
    """
    if scope.get("narrowed") or active_meeting_id:
        return scope
    inherited = inherit_scope(context.history, context.history_summary)
    if not inherited:
        return scope
    return {
        "meeting_ids": inherited,
        "narrowed": True,
        "ambiguous": len(inherited) > 1,
        "source": "history",
    }


def resolve_scope_node(state: AgentState, context: NodeContext) -> dict:
    """内容路径上的范围确定：继承历史范围，并把结论写进 trace。"""
    started = time.perf_counter()
    scope = apply_history_scope(
        dict(state.get("scope") or {}), context, state.get("active_meeting_id")
    )
    trace = append_trace(state, "resolve_scope", started, scope.get("source") or "library")
    return {"scope": scope, "trace": trace}


def clarify_node(state: AgentState, context: NodeContext) -> dict:
    """范围不唯一时反问，不做猜测（不调用模型）。"""
    started = time.perf_counter()
    from app.agent import answers_retrieval
    from app.agent.nodes.context import scope_meetings

    draft = answers_retrieval.answer_clarify(scope_meetings(state, context))
    trace = append_trace(state, "clarify", started, "范围不唯一")
    return {
        "answer_draft": draft,
        "answer_mode": "clarify",
        "synthesis_source": "template",
        "trace": trace,
    }


def create_task_node(state: AgentState, context: NodeContext) -> dict:
    """音频处理分支：建后台任务（进度仍走 `GET /api/tasks/{id}/events`）。

    对话接口目前还没有附件字段，因此没有附件时这条路径给出的是引导语；
    有了附件（程序化调用或后续版本把上传接进对话）就直接建任务。
    """
    started = time.perf_counter()
    from app.agent.answers import Draft, note_block

    attachments = state.get("attachments") or []
    if attachments and context.settings is not None:
        from app.tools.audio import ProcessAudioInput, process_audio

        created = []
        for item in attachments:
            output = process_audio(
                context.settings, ProcessAudioInput(filename=item.get("filename", "audio"))
            )
            created.append(output.task_id)
        draft = Draft(
            lead="已收到 %d 段音频，正在后台转写并整理。" % len(created),
            blocks=[note_block("处理进度会实时显示；完成后可以直接问我这场会的内容。")],
        )
        detail = "tasks=%s" % ",".join(created)
    else:
        draft = Draft(
            lead="请先把会议音频拖进输入框上传，我来负责转写、说话人识别和纪要整理。",
            blocks=[note_block("上传后可以直接提问，例如「这场会定了什么」「谁负责埋点口径」。")],
        )
        detail = "no-attachment"
    trace = append_trace(state, "create_task", started, detail)
    return {
        "answer_draft": draft,
        "answer_mode": "process_result",
        "synthesis_source": "template",
        "trace": trace,
    }


__all__ = [
    "RouteDecision",
    "clarify_node",
    "create_task_node",
    "resolve_scope_node",
    "route_node",
]
