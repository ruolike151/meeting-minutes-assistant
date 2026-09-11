"""领域模型（Pydantic v2）。

同时承担三件事（《技术方案》6.1）：
1. LLM 结构化输出的目标 schema；
2. 入库前的校验；
3. 接口的序列化格式。

两处与文档的显式偏差，README 里都记了原因：

* 片段与待办在会议内编号（`s1`、`a1`），全局定位用 `(meeting_id, id)`；
  对外暴露的待办 ID 形如 `m_20260911:a1`，接口路径因此不需要额外的会议参数。
  这样种子数据可以直接沿用原型里的 `s1` / `a1` 编号，不必做一次易错的批量重编号。
* `owner_source` 在文档的 `explicit | inferred | unknown` 之外补了
  `missing`（会上未指定）与 `confirmed`（人工确认后补录），
  这两个都是 M4 人在回路流程里真实存在的状态。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1

# 置信度低于该阈值的待办必须进「待确认区」（产品文档 5.1 / 6.1）
REVIEW_CONFIDENCE_THRESHOLD = 0.75

MeetingStatus = Literal["pending", "processing", "processed", "failed"]
ActionStatus = Literal["pending", "done", "cancelled"]
OwnerSource = Literal["explicit", "inferred", "missing", "unknown", "confirmed"]

# 文档 7.1 的 Intent。M1 由文本触发后三条，`process_audio` 由音频接口置位。
Intent = Literal["process_audio", "query_content", "query_speaker", "unknown"]

# 回答形态：Intent 决定「走哪条路」，AnswerMode 决定「装配成什么样」。
# 拆两层是为了让路由结果能在测试里直接断言（约束 C1）。
AnswerMode = Literal[
    "decisions",
    "actions",
    "risks",
    "open_questions",
    "attribution",
    "speaker_tasks",
    "speaker_speech",
    "content",
    "clarify",
    "refuse",
    "process_result",
]


def global_item_id(meeting_id: str, item_id: str) -> str:
    """待办的对外 ID：`m_20260911:a1`。"""
    return "%s:%s" % (meeting_id, item_id)


def split_item_id(value: str) -> tuple[str, str]:
    """拆分对外待办 ID，非法格式直接抛 ValueError（交给接口层转 400）。"""
    meeting_id, _, item_id = value.partition(":")
    if not meeting_id or not item_id:
        raise ValueError("待办 ID 需要形如 <meeting_id>:<item_id>")
    return meeting_id, item_id


class Participant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    role: str | None = None
    speaking_seconds: float = Field(default=0.0, ge=0)


class Segment(BaseModel):
    """转写片段：检索与证据链的最小粒度。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    meeting_id: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    speaker_id: str | None = None
    speaker_confidence: float = Field(default=0.0, ge=0, le=1)
    text: str

    @model_validator(mode="after")
    def _check_span(self) -> "Segment":
        if self.end < self.start:
            raise ValueError("片段结束时间不能早于开始时间")
        return self

    @property
    def duration(self) -> float:
        return self.end - self.start


class Meeting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    date: date
    time: str | None = None
    duration: float = Field(default=0.0, ge=0)
    status: MeetingStatus = "processed"
    audio_file: str | None = None
    participants: list[Participant] = Field(default_factory=list)
    created_at: datetime | None = None

    def speaker_name(self, speaker_id: str | None) -> str:
        for p in self.participants:
            if p.id == speaker_id:
                return p.name
        return "未识别"


class SummaryTopic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    points: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    content: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.9, ge=0, le=1)


class ActionItem(BaseModel):
    """待办：约束 C4「每条结论必须绑定片段 ID」的主要载体。

    `confidence` 是模型侧的置信度，`confirmed` 是人工侧的确认标记。
    两者分开记：人工确认只是解除「必须进待确认区」的约束，
    不会把模型原本的置信度改写成 1.0，事后仍然看得出当初为什么被拦下来。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    task: str
    owner: str | None = None
    owner_source: OwnerSource = "unknown"
    due: date | None = None
    due_text: str | None = None      # 「下周三」这类口语时间，未解析成日期前的展示值
    status: ActionStatus = "pending"
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.9, ge=0, le=1)
    needs_review: bool = False
    confirmed: bool = False
    review_reason: str | None = None

    @model_validator(mode="after")
    def _check_review_flag(self) -> "ActionItem":
        """无证据或低置信度的条目必须进待确认区，不允许静默进最终稿。

        人工确认过（confirmed=True）的条目不再受该约束，
        因为此时把关的是人，而不是模型的置信度。
        """
        if self.needs_review or self.confirmed:
            return self
        if not self.evidence:
            raise ValueError("needs_review=False 的待办必须带证据片段")
        if self.confidence < REVIEW_CONFIDENCE_THRESHOLD:
            raise ValueError(
                "置信度 %.2f 低于阈值 %.2f，必须标记 needs_review"
                % (self.confidence, REVIEW_CONFIDENCE_THRESHOLD)
            )
        return self


class OpenQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    content: str
    evidence: list[str] = Field(default_factory=list)


class Risk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    content: str
    evidence: list[str] = Field(default_factory=list)


class Minutes(BaseModel):
    """结构化纪要：整份 JSON 落 `minutes.payload` 一列。"""

    model_config = ConfigDict(extra="forbid")

    meeting_id: str
    schema_version: int = SCHEMA_VERSION
    summary: list[SummaryTopic] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    updated_at: datetime | None = None


class Citation(BaseModel):
    """回答里的引用：前端据此渲染可点击 chip 并回跳原声。"""

    meeting_id: str
    meeting_title: str
    segment_id: str
    speaker: str
    start: float = Field(ge=0)
    label: str

    @property
    def key(self) -> str:
        return "%s:%s" % (self.meeting_id, self.segment_id)


class AnswerItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    text: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    warn: bool = False
    meta: str | None = None


class AnswerBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["quote", "note"] = "quote"
    title: str | None = None
    text: str | None = None
    items: list[AnswerItem] = Field(default_factory=list)


class TraceStep(BaseModel):
    """可观测（《技术方案》13）：编排节点的耗时与结论。"""

    name: str
    ms: float = Field(ge=0)
    detail: str | None = None


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead: str
    blocks: list[AnswerBlock] = Field(default_factory=list)
    footnote: str | None = None
    intent: Intent
    answer_mode: AnswerMode
    scope_meeting_ids: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    trace: list[TraceStep] = Field(default_factory=list)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    role: Literal["user", "assistant", "system"]
    content: str
    evidence: list[str] = Field(default_factory=list)   # "meeting_id:segment_id"
    answer: Answer | None = None                        # 助手消息保留完整渲染载荷
    created_at: datetime


class Conversation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    created_at: datetime
    messages: list[Message] = Field(default_factory=list)
