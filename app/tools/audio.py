"""音频工具：process_audio（M1 走离线感知后端）。

任务登记与执行分开：
* `register_audio` 落盘并建任务，立刻返回 task_id；
* `execute_task` 真正跑转写 -> 声纹 -> 纪要，由 `GET /api/tasks/{id}/events`
  在推送阶段进度时逐阶段调用。
这样接口层能一边推 SSE 一边推进任务，不需要后台线程（M2 换成真实任务队列）。
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.domain.models import Meeting
from app.perception.offline import STAGES, OfflinePerception, title_from_filename
from app.store.files import save_audio
from app.store.repos import MeetingRepo, new_id, utcnow
from app.store.tasks import TaskState, registry


class ProcessAudioInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(description="原始音频文件名，用于生成会议标题")


class ProcessAudioOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    filename: str
    size: int
    stages: list[str] = Field(default_factory=list)
    status: str = "queued"


def register_audio(settings: Settings, filename: str, data: bytes) -> TaskState:
    """保存上传文件并建立任务。"""
    path = save_audio(data, filename, settings.audio_dir)
    return registry.create(filename=path.name, path=path, size=len(data), stages=list(STAGES))


def process_audio(settings: Settings, payload: ProcessAudioInput) -> ProcessAudioOutput:
    """工具层入口：只建任务，不阻塞等待处理完成。"""
    task = registry.create(
        filename=payload.filename,
        path=Path(settings.audio_dir) / payload.filename,
        size=0,
        stages=list(STAGES),
    )
    return ProcessAudioOutput(
        task_id=task.id, filename=task.filename, size=task.size, stages=task.stages
    )


def execute_task(
    conn: sqlite3.Connection,
    task: TaskState,
    *,
    meeting_id: str | None = None,
    today: date | None = None,
) -> Meeting:
    """跑完离线感知后端并落库，返回新建会议。"""
    task.status = "running"
    backend = OfflinePerception()
    now = utcnow()
    meeting, segments, minutes = backend.process(
        task.path,
        meeting_id=meeting_id or new_id("m"),
        title=title_from_filename(task.filename),
        today=today,
        time_text=now.astimezone().strftime("%H:%M"),
    )
    MeetingRepo(conn).create(meeting, segments, minutes)
    task.status = "done"
    task.stage_index = len(task.stages)
    task.meeting_id = meeting.id
    return meeting


def fail_task(task: TaskState, error: Exception) -> None:
    task.status = "failed"
    task.error = "%s: %s" % (type(error).__name__, error)
