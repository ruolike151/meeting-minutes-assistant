"""编排门面：驱动 LangGraph 状态图，对外保持 M1 的事件契约。

这一层只做四件事：

1. 组装 `NodeContext`（配置 → LLM 客户端、文本编码器、会话历史）；
2. 用 `graph.stream(..., stream_mode="updates")` 跑图，把节点名翻译成 `stage` 事件；
3. 把最终结果翻译成 `citation` / `delta` / `done`（与 M1 逐字段一致，
   因此 `web/` 前端在 M3 期间一行都不用改）；
4. `respond()` 只是把同一个生成器跑完取终态——JSON 接口与 SSE 接口共享同一份实现，
   不会出现"两条路径行为不一致"。

`settings=None` 时等价于 M1 的"没有模型、没有向量"：规则路由 + 关键词检索 + 模板装配。
这条默认路径让全部既有测试与无 Key 机器都能跑通（约束 C3）。
"""

from __future__ import annotations

import sqlite3
from typing import Iterator

from app.agent.checkpoint import checkpoint_config, open_checkpointer
from app.agent.graph import NODE_LABELS, build_graph
from app.agent.history import compress_history, load_history
from app.agent.nodes import NodeContext
from app.agent.persist import ChatResult, PipelineError, plain_text  # noqa: F401 - 兼容旧导入
from app.agent.verify import flatten_citations, verify_evidence  # noqa: F401 - 兼容旧导入
from app.config import DEFAULT_HISTORY_ROUNDS, Settings
from app.llm.client import LLMClient, LLMUnavailable
from app.llm.factory import get_client
from app.llm.fake import FakeLLMClient
from app.retrieval.encoder import EncoderUnavailable, TextEncoder, select_encoder
from app.store.repos import new_id


def respond(
    conn: sqlite3.Connection,
    user_input: str,
    *,
    conversation_id: str | None = None,
    active_meeting_id: str | None = None,
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    encoder: TextEncoder | None = None,
) -> ChatResult:
    """跑完整条链路并返回结果（供 JSON 接口与测试使用）。"""
    result: ChatResult | None = None
    for event, payload in stream(
        conn,
        user_input,
        conversation_id=conversation_id,
        active_meeting_id=active_meeting_id,
        settings=settings,
        llm=llm,
        encoder=encoder,
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
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    encoder: TextEncoder | None = None,
) -> Iterator[tuple[str, object]]:
    """产出 (事件名, 载荷)：stage -> citation -> delta -> done（《技术方案》11 节）。"""
    context = build_context(
        conn,
        settings,
        conversation_id=conversation_id,
        llm=llm,
        encoder=encoder,
    )
    # 检查点要有 thread_id 才能用（LangGraph 的硬性要求）。第一轮没有会话 ID 时
    # 先生成一个：它同时是这个 thread 的键与最终落库的会话 ID，两者必须一致，
    # 否则第二轮恢复不到上一轮的状态。
    saver = open_checkpointer(settings)
    thread_id = conversation_id
    if saver is not None and not thread_id:
        thread_id = new_id("c")
    graph = build_graph(context, checkpointer=saver)
    state = {
        "user_input": user_input,
        "conversation_id": thread_id,
        "thread_id": thread_id,
        "active_meeting_id": active_meeting_id,
        "attachments": [],
        "history": [message.model_dump() for message in context.history],
        "history_summary": context.history_summary,
        "trace": [],
    }

    result: ChatResult | None = None
    config = checkpoint_config(thread_id) or None
    for chunk in graph.stream(state, config, stream_mode="updates"):
        for node_name, update in chunk.items():
            yield "stage", {"node": node_name, "label": NODE_LABELS.get(node_name, node_name)}
            if isinstance(update, dict) and update.get("result") is not None:
                result = update["result"]
    if result is None:
        raise PipelineError("编排链路没有产出结果")

    for citation in result.answer.citations:
        yield "citation", citation.model_dump(mode="json")
    yield "delta", {"text": result.answer.lead}
    yield "done", result


def build_context(
    conn: sqlite3.Connection,
    settings: Settings | None,
    *,
    conversation_id: str | None = None,
    llm: LLMClient | None = None,
    encoder: TextEncoder | None = None,
) -> NodeContext:
    """按配置组装节点上下文（模型与编码器都遵循"不可用就降级"的原则）。"""
    encoder_reason = ""
    usage = None
    if settings is not None:
        if llm is None:
            try:
                llm = get_client(settings, conn)
            except LLMUnavailable:
                # 配置有问题（例如 provider 非法）时按"没有模型"处理：
                # 回答照常产出，原因由 llm_mode_reason / 冒烟脚本暴露。
                llm = FakeLLMClient("", available=False)
        # 记账必须用客户端自己的累加器：用量是 `CachedLLMClient` 在调用时就地记下的，
        # 另起一个 UsageTracker 会让 trace 里的 token / 费用永远是 0（M3 首版就踩了这个坑）。
        usage = getattr(llm, "usage", None)
        if encoder is None:
            try:
                selection = select_encoder(settings)
                encoder = selection.encoder
                encoder_reason = selection.fallback_reason or ""
            except EncoderUnavailable as error:
                encoder = None
                encoder_reason = "编码器不可用，已退回关键词检索：%s" % error

    if usage is None and llm is not None:
        usage = getattr(llm, "usage", None)
    context = NodeContext.from_settings(
        conn,
        settings,
        llm=llm,
        encoder=encoder,
        encoder_reason=encoder_reason,
        usage=usage,
    )

    rounds = settings.history_rounds if settings is not None else DEFAULT_HISTORY_ROUNDS
    if settings is not None and conversation_id:
        compress_history(
            llm,
            conn,
            conversation_id,
            model=settings.llm_model_answer,
            usage=context.usage,
        )
    history, summary = load_history(conn, conversation_id, recent_rounds=rounds)
    context.history = history
    context.history_summary = summary
    return context


__all__ = [
    "ChatResult",
    "PipelineError",
    "build_context",
    "flatten_citations",
    "plain_text",
    "respond",
    "stream",
    "verify_evidence",
]
