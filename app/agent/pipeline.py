"""M1 编排链路：路由 -> 范围 -> 检索/装配 -> 证据校验 -> 落库。

为什么是一个生成器：`stream()` 在每个节点开始前产出 stage 事件，
HTTP 层把它转成 SSE，前端因此能显示真实阶段进度，而不是假进度条。
`respond()` 只是把同一个生成器跑完取最后一个结果，两条路径共享一份实现，
不会出现「JSON 接口和 SSE 接口行为不一致」的问题。

M3 会把各节点装进 LangGraph 的 StateGraph（节点函数不变），
并用 checkpointer + interrupt 支撑待确认区的挂起与恢复。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Iterator

from app.agent import answers_retrieval as retrieval_answers
from app.agent.answers import (
    AnswerContext,
    Draft,
    answer_actions,
    answer_decisions,
    answer_open_questions,
    answer_risks,
    answer_speaker_speech,
    answer_speaker_tasks,
    not_found,
)
from app.agent.routing import RouteResult, route
from app.domain.models import (
    Answer,
    AnswerBlock,
    AnswerItem,
    Citation,
    Message,
    TraceStep,
)
from app.retrieval.pipeline import retrieve
from app.store.conversations_repo import ConversationRepo
from app.store.repos import MeetingRepo, new_id, utcnow


@dataclass
class ChatResult:
    conversation_id: str
    user_message: Message
    assistant_message: Message
    answer: Answer
    trace: list[TraceStep] = field(default_factory=list)


class PipelineError(RuntimeError):
    """编排层内部错误。接口层统一转 500，并保留原始信息便于排查。"""


def respond(
    conn: sqlite3.Connection,
    user_input: str,
    *,
    conversation_id: str | None = None,
    active_meeting_id: str | None = None,
) -> ChatResult:
    """跑完整条链路并返回结果（供 JSON 接口与测试使用）。"""
    result: ChatResult | None = None
    for event, payload in stream(
        conn,
        user_input,
        conversation_id=conversation_id,
        active_meeting_id=active_meeting_id,
    ):
        if event == "done":
            result = payload  # type: ignore[assignment]
    if result is None:
        raise PipelineError("编排链路没有产出结果")
    return result


def stream(
    conn: sqlite3.Connection,
    user_input: str,
    *,
    conversation_id: str | None = None,
    active_meeting_id: str | None = None,
) -> Iterator[tuple[str, object]]:
    """产出 (事件名, 载荷)：stage -> citation -> delta -> done。

    事件名与《技术方案》11 节的 SSE 契约一致。
    """
    trace: list[TraceStep] = []
    meetings = MeetingRepo(conn).list()

    # -- 节点 1：route -------------------------------------------------
    started = time.perf_counter()
    yield "stage", {"node": "route", "label": "理解意图与查询范围"}
    route_result = route(user_input, meetings=meetings, active_meeting_id=active_meeting_id)
    scope_ids = set(route_result.scope.meeting_ids)
    scope_meetings = [meeting for meeting in meetings if meeting.id in scope_ids]
    trace.append(
        TraceStep(
            name="route",
            ms=_ms(started),
            detail="%s / %s" % (route_result.intent, route_result.answer_mode),
        )
    )

    # -- 节点 2：assemble ---------------------------------------------
    started = time.perf_counter()
    yield "stage", {"node": "assemble", "label": "装配证据与结论"}
    ctx = AnswerContext(conn=conn, meetings=scope_meetings, user_input=user_input)
    if meetings:
        draft, answer_mode = _build_draft(ctx, route_result, user_input, scope_meetings)
    else:
        draft, answer_mode = _empty_library(), "refuse"
    trace.append(TraceStep(name="assemble", ms=_ms(started), detail=answer_mode))

    # -- 节点 3：verify_evidence（约束 C4）-----------------------------
    started = time.perf_counter()
    yield "stage", {"node": "verify_evidence", "label": "校验每条结论的证据"}
    verified, dropped, unsupported = verify_evidence(ctx, draft)
    trace.append(
        TraceStep(
            name="verify_evidence",
            ms=_ms(started),
            detail="丢弃无效引用 %d / 无证据结论 %d" % (dropped, unsupported),
        )
    )

    # -- 节点 4：persist ----------------------------------------------
    citations = flatten_citations(verified)
    answer = Answer(
        lead=verified.lead,
        blocks=verified.blocks,
        footnote=verified.footnote,
        intent=route_result.intent,
        answer_mode=answer_mode,  # type: ignore[arg-type]
        scope_meeting_ids=[meeting.id for meeting in scope_meetings],
        citations=citations,
        trace=list(trace),
    )
    started = time.perf_counter()
    yield "stage", {"node": "persist", "label": "写入会话记录"}
    result = _persist(conn, user_input, answer, conversation_id)
    trace.append(TraceStep(name="persist", ms=_ms(started), detail=result.conversation_id))
    answer.trace = list(trace)
    result.trace = list(trace)
    # 第一次写库时 trace 还缺 persist 这一步，补写一次把完整链路带上
    result.assistant_message = result.assistant_message.model_copy(update={"answer": answer})
    ConversationRepo(conn).append(result.conversation_id, result.assistant_message)

    for citation in citations:
        yield "citation", citation.model_dump(mode="json")
    yield "delta", {"text": answer.lead}
    yield "done", result


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _empty_library() -> Draft:
    return Draft(
        lead="会议库还是空的，先上传一段会议录音，我再帮你整理和回答。",
        blocks=[
            AnswerBlock(
                kind="note", text="上传音频后会自动完成转写、说话人识别与纪要生成。"
            )
        ],
    )


def _build_draft(
    ctx: AnswerContext,
    route_result: RouteResult,
    user_input: str,
    scope_meetings,
) -> tuple[Draft, str]:
    """按回答形态分派，返回 (草稿, 最终形态)。

    最终形态可能与路由形态不同：发言人查不到待办、检索证据不足，
    都会收敛成 refuse，这样「是否拒答」在接口层是一个可断言的枚举。
    """
    mode = route_result.answer_mode
    speaker_name = route_result.speaker_name or "该成员"

    if mode == "clarify":
        return retrieval_answers.answer_clarify(scope_meetings), "clarify"
    if mode == "speaker_tasks" and route_result.speaker_id:
        draft = answer_speaker_tasks(ctx, route_result.speaker_id, speaker_name)
        return draft, "speaker_tasks" if draft.blocks else "refuse"
    if mode == "speaker_speech" and route_result.speaker_id:
        draft = answer_speaker_speech(ctx, route_result.speaker_id, speaker_name)
        return draft, "speaker_speech" if draft.blocks else "refuse"
    if mode == "decisions":
        return answer_decisions(ctx), "decisions"
    if mode == "actions":
        return answer_actions(ctx), "actions"
    if mode == "risks":
        return answer_risks(ctx), "risks"
    if mode == "open_questions":
        return answer_open_questions(ctx), "open_questions"

    # attribution / content：先检索，证据不足直接拒答，不进入合成
    retrieval = retrieve(ctx.conn, user_input, meeting_ids=[m.id for m in scope_meetings])
    if retrieval.refused:
        return retrieval_answers.answer_refuse(ctx, retrieval), "refuse"
    if mode == "attribution":
        return retrieval_answers.answer_attribution(ctx, retrieval), "attribution"
    if mode == "content":
        return retrieval_answers.answer_content(ctx, retrieval), "content"
    return not_found(ctx), "refuse"


def verify_evidence(ctx: AnswerContext, draft: Draft) -> tuple[Draft, int, int]:
    """证据校验后置（《技术方案》7.4）。

    生成完不直接返回，而是把每条结论引用的片段再核一遍：
    引用不存在的片段直接丢掉；一条本该有证据的结论如果引用全丢，
    就标记为待确认，避免无法核对的结论进入最终稿。
    """
    dropped = 0
    unsupported = 0
    blocks: list[AnswerBlock] = []
    for block in draft.blocks:
        if block.kind == "note":
            blocks.append(block)
            continue
        items: list[AnswerItem] = []
        for item in block.items:
            keep = [
                citation
                for citation in item.citations
                if ctx.segment(citation.meeting_id, citation.segment_id) is not None
            ]
            dropped += len(item.citations) - len(keep)
            if item.citations and not keep:
                unsupported += 1
                item = item.model_copy(update={"warn": True})
            items.append(item.model_copy(update={"citations": keep}))
        blocks.append(block.model_copy(update={"items": items}))
    return Draft(lead=draft.lead, blocks=blocks, footnote=draft.footnote), dropped, unsupported


def flatten_citations(draft: Draft) -> list[Citation]:
    """去重后的引用清单：前端渲染与评测都用这一份。"""
    seen: set[str] = set()
    citations: list[Citation] = []
    for block in draft.blocks:
        for item in block.items:
            for citation in item.citations:
                key = "%s:%s" % (citation.meeting_id, citation.segment_id)
                if key in seen:
                    continue
                seen.add(key)
                citations.append(citation)
    return citations


def plain_text(answer: Answer) -> str:
    """把结构化回答压成纯文本，供会话导出与多轮上下文使用。"""
    parts = [answer.lead]
    for block in answer.blocks:
        if block.kind == "note" and block.text:
            parts.append(block.text)
            continue
        if not block.items:
            continue
        parts.append("【%s】" % (block.title or ""))
        for item in block.items:
            parts.append("- " + "：".join(part for part in (item.title, item.text) if part))
    if answer.footnote:
        parts.append(answer.footnote)
    return "\n".join(parts)


def _persist(
    conn: sqlite3.Connection, user_input: str, answer: Answer, conversation_id: str | None
) -> ChatResult:
    repo = ConversationRepo(conn)
    conversation = repo.ensure(conversation_id)
    now = utcnow()
    evidence = ["%s:%s" % (c.meeting_id, c.segment_id) for c in answer.citations]
    user_message = Message(id=new_id("msg"), role="user", content=user_input, created_at=now)
    assistant_message = Message(
        id=new_id("msg"),
        role="assistant",
        content=plain_text(answer),
        evidence=evidence,
        answer=answer,
        created_at=now,
    )
    repo.append(conversation.id, user_message)
    repo.append(conversation.id, assistant_message)
    return ChatResult(
        conversation_id=conversation.id,
        user_message=user_message,
        assistant_message=assistant_message,
        answer=answer,
    )
