"""AgentState 定义（《技术方案》7.1）。

M3 起这个状态真正流进了 LangGraph 的 StateGraph：每个节点只返回自己改动的字段，
框架负责合并。字段按「路由 → 范围 → 证据 → 草稿 → 校验 → 落库」的链路顺序排列，
与 `nodes/` 里的节点一一对应。

注意两点：

* `scope` 用普通 dict（不是 routing.Scope 那个 frozen dataclass）：
  状态要能在节点之间自由合并，dataclass 换成 dict 更省事，转换只在 `route` 节点做一次；
* `trace` 由节点自己追加（每个节点返回完整列表），因此不依赖框架的 reducer。
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
from app.retrieval.pipeline import RetrievalResult


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
    active_meeting_id: str | None   # 界面当前锁定的会议
    conversation_id: str | None
    history: list[dict]             # 压缩后的会话上下文（role + content）
    history_summary: str            # 更早轮次的摘要

    # -- 路由与范围 --
    intent: Intent
    answer_mode: str                # 决定回答装配形态（见 domain.AnswerMode）
    scope: Scope
    speaker_id: str | None
    speaker_name: str | None
    route_source: str               # llm | rules
    route_reason: str
    route_rejected: list[str]       # 模型给出但被护栏丢掉的内容（如不存在的会议 ID）

    # -- 检索与证据 --
    retrieval: RetrievalResult
    evidence: list[Segment]
    encoder: str | None
    encoder_reason: str
    enough_evidence: bool
    grade_reason: str

    # -- 生成与校验 --
    draft: Minutes | None
    answer_draft: object            # synthesize 产出的 Draft（dataclass）
    synthesis_source: str           # llm | template
    verify_detail: str
    dropped_citations: int
    unsupported_items: int

    # -- 产出 --
    answer: Answer | None
    citations: list[str]
    result: object                  # ChatResult（落库后的最终结果）
    clarify_question: str
    retry_count: int
    trace: list[TraceStep]
    usage: dict
