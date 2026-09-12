"""HTTP 请求 / 响应 DTO。

`MeetingDetail` 是给界面用的聚合视图：一次请求拿齐会议、参会人、片段、纪要条目
与待确认清单，避免前端为一个详情页发四五个请求。
工具契约（get_minutes / list_meetings 等）另有自己的 DTO，见 app/tools/。
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import (
    ActionItem,
    Answer,
    Citation,
    Decision,
    Message,
    OpenQuestion,
    Participant,
    Risk,
    Segment,
    SummaryTopic,
)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, description="用户输入，文字提问")
    conversation_id: str | None = Field(default=None, description="留空则新建会话")
    meeting_id: str | None = Field(
        default=None, description="会话当前锁定的会议，用于限定查询范围"
    )


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    message: Message
    answer: Answer


class ReviewItem(BaseModel):
    """待确认区条目：把待办与决议展开成前端可直接渲染的形状。

    M4 起 `kind` 区分两类条目（决策 ②=A）：待办可改负责人 / 截止时间，
    决议只能确认或忽略（它没有负责人与截止时间）。
    """

    model_config = ConfigDict(extra="forbid")

    id: str                      # 全局 ID：m_20260911:a1
    meeting_id: str
    meeting_title: str
    kind: Literal["action_item", "decision"] = "action_item"
    task: str                    # 待办的 task / 决议的正文，统一叫 task 便于前端复用
    owner: str | None = None
    owner_name: str
    due: date | None = None
    due_text: str | None = None
    reason: str
    confidence: float
    editable: bool = True
    citations: list[Citation] = Field(default_factory=list)


class MeetingDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    date: date
    time: str | None = None
    duration: float = 0
    status: str
    audio_file: str | None = None
    participants: list[Participant] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)
    summary: list[SummaryTopic] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    review_items: list[ReviewItem] = Field(default_factory=list)


class ActionItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "done", "cancelled"] | None = None
    owner: str | None = None
    due: str | None = None


class ReviewResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # `decision` 是 M1–M3 的字段名，保留兼容；`action` 支持 M4 新增的 edit
    decision: Literal["confirm", "dismiss"] | None = None
    action: Literal["confirm", "dismiss", "edit"] | None = None
    owner: str | None = Field(default=None, description="人工补录的负责人 speaker_id")
    due: str | None = Field(default=None, description="人工补录的截止日期 YYYY-MM-DD")
    note: str | None = None

    def resolved_action(self) -> str:
        """取本次动作；两个字段都没给时按 confirm 处理（M1 的默认行为）。"""
        return self.action or self.decision or "confirm"


class ReviewResolveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    item_id: str
    decision: str
    meeting_id: str | None = None
    remaining: int = 0
    finalized: bool = False
    message: str = ""


class AudioUploadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    filename: str
    size: int
    stages: list[str] = Field(default_factory=list)


class TaskStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: str
    stage_index: int = 0
    stages: list[str] = Field(default_factory=list)
    progress: float = 0
    meeting_id: str | None = None
    error: str | None = None
    notes: str | None = None
