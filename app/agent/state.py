"""AgentState 定义（《技术方案》7.1）。

M1 的编排是顺序函数，还没有 checkpointer，但状态结构先按文档定下来：
M3 用 LangGraph 装配时，节点签名与这里的字段一一对应，不需要重新设计状态。
"""

from __future__ import annotations

from typing import TypedDict

from app.domain.models import (
    Answer,
    Intent,
    Minutes,
    Segment,
    TraceStep,
)


class Attachment(TypedDict, total=False):
    """待处理音频。"""

    filename: str
    path: str
    size: int


class Scope(TypedDict, total=False):
    """查询范围：哪几场会议。"""

    meeting_ids: list[str]
    narrowed: bool          # 问题里是否明确提到了某场会议
    ambiguous: bool
    source: str             # explicit | active | library


class AgentState(TypedDict, total=False):
    thread_id: str                  # = conversation_id，M3 作为 checkpointer 的键
    user_input: str
    attachments: list[Attachment]
    history: list[dict]             # 压缩后的会话上下文
    intent: Intent
    answer_mode: str                # 决定回答装配形态（见 domain.AnswerMode）
    scope: Scope
    evidence: list[Segment]
    draft: Minutes | None
    answer: Answer | None
    citations: list[str]
    clarify_question: str
    retry_count: int
    trace: list[TraceStep]
    usage: dict
