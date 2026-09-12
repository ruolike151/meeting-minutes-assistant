"""节点上下文与共用小工具（M3）。

`NodeContext` 是一次编排运行的全部外部依赖：数据库连接、配置、LLM 客户端、
文本编码器、会话历史。把它显式传进每个节点，而不是让节点自己去 import 全局对象，
换来两件事：

* 节点可以在测试里被单独调用，依赖全部可替换（假 LLM、假编码器）；
* 节点之间没有隐式共享状态，图重跑或分支切换不会互相污染。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Callable

from app.agent.state import AgentState
from app.config import RetrievalSettings, Settings
from app.domain.models import Meeting, TraceStep
from app.llm.cache import UsageTracker
from app.llm.client import ChatMessage, LLMClient
from app.retrieval.encoder import TextEncoder
from app.store.repos import MeetingRepo


def ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


@dataclass
class NodeContext:
    conn: sqlite3.Connection
    settings: Settings | None = None
    llm: LLMClient | None = None
    encoder: TextEncoder | None = None
    history: list[ChatMessage] = field(default_factory=list)
    history_summary: str = ""
    usage: UsageTracker = field(default_factory=UsageTracker)
    encoder_reason: str = ""
    route_mode: str = "rules"
    verify_mode: str = "rule"
    model_route: str = ""
    model_answer: str = ""
    model_minutes: str = ""
    retrieval_settings: RetrievalSettings = field(default_factory=RetrievalSettings)
    _meetings: list[Meeting] | None = None
    # 人在回路：挂起函数由 `graph.py` 注入 `langgraph.types.interrupt`。
    # 节点因此不需要 import 框架（纪律：只有 graph.py 与 checkpoint.py 能 import），
    # 测试里注入一个记录调用的假函数就能断言"闸门确实挂起了"。
    interrupt_fn: Callable[[dict], object] | None = None
    # 重排器（M5）：默认空实现，行为与 M3 一致；
    # 由 `pipeline.build_context` 按 MA_RERANK_MODE 注入。
    reranker: object | None = None

    @classmethod
    def from_settings(
        cls,
        conn: sqlite3.Connection,
        settings: Settings | None,
        *,
        llm: LLMClient | None = None,
        encoder: TextEncoder | None = None,
        encoder_reason: str = "",
        history: list[ChatMessage] | None = None,
        history_summary: str = "",
        usage: UsageTracker | None = None,
    ) -> "NodeContext":
        """按配置组装上下文；`settings=None` 时等价于"没有模型、没有向量"的 M1 行为。"""
        if settings is None:
            return cls(
                conn=conn,
                llm=llm,
                encoder=encoder,
                encoder_reason=encoder_reason,
                history=list(history or []),
                history_summary=history_summary,
                usage=usage or UsageTracker(),
            )
        return cls(
            conn=conn,
            settings=settings,
            llm=llm,
            encoder=encoder,
            history=list(history or []),
            history_summary=history_summary,
            encoder_reason=encoder_reason,
            route_mode=settings.route_mode,
            verify_mode=settings.verify_mode,
            model_route=settings.llm_model_route,
            model_answer=settings.llm_model_answer,
            model_minutes=settings.llm_model_minutes,
            retrieval_settings=settings.retrieval,
            usage=usage or UsageTracker(),
        )

    # -- 读路径 ---------------------------------------------------------

    def library(self) -> list[Meeting]:
        """会议库（一次运行内缓存，避免每个节点各查一遍）。"""
        if self._meetings is None:
            self._meetings = MeetingRepo(self.conn).list()
        return self._meetings

    def llm_available(self) -> bool:
        return self.llm is not None and bool(getattr(self.llm, "available", False))

    def history_payload(self) -> str:
        """把最近几轮对话拼成一段文本（供提示词使用）。"""
        lines = []
        if self.history_summary:
            lines.append("更早的对话摘要：%s" % self.history_summary)
        for message in self.history:
            lines.append("%s：%s" % (message.role, message.content))
        return "\n".join(lines)


def append_trace(
    state: AgentState, name: str, started: float, detail: str | None = None
) -> list[TraceStep]:
    """在状态里追加一步 trace（每个节点返回完整列表，不依赖框架的 reducer）。"""
    trace = list(state.get("trace") or [])
    trace.append(TraceStep(name=name, ms=ms(started), detail=detail))
    return trace


def scope_meetings(state: AgentState, context: NodeContext) -> list[Meeting]:
    """把状态里的会议范围解析成会议对象列表。"""
    scope = state.get("scope") or {}
    ids = list(scope.get("meeting_ids") or [])
    library = context.library()
    if not ids:
        return library
    wanted = set(ids)
    return [meeting for meeting in library if meeting.id in wanted]
