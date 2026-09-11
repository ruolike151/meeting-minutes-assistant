"""音频上传与任务进度接口。

M1 的任务执行是「订阅即推进」：`GET /api/tasks/{id}/events` 一边推阶段事件
一边调用离线感知后端。这样没有后台线程也能演示完整链路，
M2 换成真实任务队列时接口形状不变。
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Iterator

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.api.deps import get_settings
from app.api.schemas import AudioUploadResponse, TaskStatus
from app.api.sse import SSE_HEADERS, sse_event
from app.config import Settings
from app.store.db import connect
from app.store.tasks import TaskState, registry
from app.tools.audio import execute_task, fail_task, register_audio

router = APIRouter(prefix="/api", tags=["audio"])

# 阶段之间的小停顿：离线后端是瞬时完成的，留一点间隔前端才看得到进度。
# 真实转写（M2）本身要几十秒，这个开关只是为了让演示与测试都舒服。
STAGE_DELAY_ENV = "MA_STAGE_DELAY"
DEFAULT_STAGE_DELAY = 0.12


def stage_delay() -> float:
    try:
        return max(0.0, float(os.environ.get(STAGE_DELAY_ENV, DEFAULT_STAGE_DELAY)))
    except (TypeError, ValueError):
        return DEFAULT_STAGE_DELAY


def to_status(task: TaskState) -> TaskStatus:
    return TaskStatus(
        task_id=task.id,
        status=task.status,
        stage_index=task.stage_index,
        stages=task.stages,
        progress=task.progress,
        meeting_id=task.meeting_id,
        error=task.error,
    )


@router.post("/audio", response_model=AudioUploadResponse)
async def upload_audio(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
) -> AudioUploadResponse:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="音频内容为空")
    task = register_audio(settings, file.filename or "meeting_audio.bin", data)
    return AudioUploadResponse(
        task_id=task.id, filename=task.filename, size=task.size, stages=task.stages
    )


@router.get("/tasks/{task_id}", response_model=TaskStatus)
def get_task(task_id: str, settings: Settings = Depends(get_settings)) -> TaskStatus:
    task = registry.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return to_status(task)


@router.get("/tasks/{task_id}/events")
def task_events(
    task_id: str, settings: Settings = Depends(get_settings)
) -> StreamingResponse:
    def encode(event: str, data: object) -> str:
        return sse_event(event, data)

    def generator() -> Iterator[str]:
        task = registry.get(task_id)
        if task is None:
            yield encode("error", {"message": "任务不存在"})
            return
        if task.status == "done" and task.meeting_id:
            yield encode("result", {"meeting_id": task.meeting_id, "status": task.status})
            yield encode("done", {"task_id": task.id, "meeting_id": task.meeting_id})
            return

        # SSE 在独立线程里迭代，必须自己开连接（SQLite 连接不能跨线程复用）
        conn = connect(settings.db_path)
        delay = stage_delay()
        try:
            total = len(task.stages)
            for index, label in enumerate(task.stages):
                task.stage_index = index
                yield encode(
                    "stage",
                    {
                        "index": index,
                        "label": label,
                        "progress": round(index / total, 4) if total else 0,
                    },
                )
                if delay:
                    time.sleep(delay)
            try:
                meeting = execute_task(conn, task)
            except Exception as error:  # noqa: BLE001 - 任务失败要转成事件而不是 500
                fail_task(task, error)
                yield encode("error", {"message": task.error or "处理失败"})
                return
            yield encode("result", {"meeting_id": meeting.id, "title": meeting.title})
            yield encode("done", {"task_id": task.id, "meeting_id": meeting.id})
        finally:
            conn.close()

    return StreamingResponse(
        generator(), media_type="text/event-stream", headers=SSE_HEADERS
    )
