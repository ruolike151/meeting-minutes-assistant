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
    """待确认区条目：把 needs_review 的待办展开成前端可直接渲染的形状。"""

    model_config = ConfigDict(extra="forbid")

    id: str                      # 全局 ID：m_20260911:a1
    meeting_id: str
    meeting_title: str
    task: str
    owner: str | None = None
    owner_name: str
    due_text: str | None = None
    reason: str
    confidence: float
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

    decision: Literal["confirm", "dismiss"]
    owner: str | None = Field(default=None, description="人工补录的负责人 speaker_id")
    due: str | None = Field(default=None, description="人工补录的截止日期 YYYY-MM-DD")
    note: str | None = None


class ReviewResolveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    item_id: str
    decision: str
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
