"""register_speaker 工具（《技术方案》8 节的契约，M1 未实现，M2 补齐）。

入参出参都是 Pydantic 模型，实现是普通函数——因此它既能被编排层的模型调用，
也能被 CLI 与测试直接调用（约束 C1/C3）。
"""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.perception.embedding import create_encoder
from app.perception.enroll import enroll_speaker


class RegisterSpeakerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="发言人姓名或角色名，例如「项目经理」")
    audio_path: str = Field(description="单人注册音频路径，建议 30-60 秒单人清晰语音")
    role: str | None = Field(default=None, description="角色说明，可省略")


class RegisterSpeakerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker_id: str
    name: str
    role: str | None = None
    encoder: str
    dim: int
    vector_count: int


def register_speaker(
    settings: Settings, conn: sqlite3.Connection, payload: RegisterSpeakerInput
) -> RegisterSpeakerOutput:
    """注册一个发言人的声纹；同名会覆盖（避免同一个人两张声纹）。"""
    encoder = create_encoder(settings)
    record = enroll_speaker(
        settings,
        conn,
        name=payload.name,
        audio_path=payload.audio_path,
        role=payload.role,
        encoder=encoder,
    )
    return RegisterSpeakerOutput(
        speaker_id=record.id,
        name=record.name,
        role=record.role,
        encoder=record.encoder,
        dim=record.dim,
        vector_count=record.vector_count,
    )