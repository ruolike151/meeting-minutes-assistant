"""音频上传与任务进度接口（M2 T6：后台执行 + 轮询进度）。

M1 是「订阅即推进」：只有客户端订阅 SSE 才真正开始处理，断开连接任务就没人推进。
M2 改成：

* `POST /api/audio` 落盘、建任务行、提交后台 worker，立刻返回 task_id；
* 后台单线程池跑感知后端，逐阶段把进度写进 `tasks` 表；
* `GET /api/tasks/{id}/events` 只轮询任务行，把状态翻译成 SSE 事件。

好处是页面刷新、断网重连都不影响处理（真实转写要几十秒到几分钟）。
客户端晚连也能看到完整流水线：已经到达的阶段会被逐条补发。
"""

from __future__ import annotations

import os
import time
from typing import Iterator

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.api.deps import get_settings
from app.api.schemas import AudioUploadResponse, TaskStatus
from app.api.sse import SSE_HEADERS, sse_event
from app.config import Settings
from app.store.db import connect
from app.store.tasks import TaskState
from app.store.tasks_repo import TaskRepo
from app.tools.audio import register_audio

router = APIRouter(prefix="/api", tags=["audio"])

# 轮询间隔：本地 SQLite 查询是微秒级，50ms 足够让阶段进度看起来是实时的
POLL_SECONDS = 0.05
TASK_TIMEOUT_ENV = "MA_TASK_TIMEOUT"
DEFAULT_TASK_TIMEOUT = 1800.0


def task_timeout() -> float:
    try:
        return max(1.0, float(os.environ.get(TASK_TIMEOUT_ENV, DEFAULT_TASK_TIMEOUT)))
    except (TypeError, ValueError):
        return DEFAULT_TASK_TIMEOUT


def to_status(task: TaskState) -> TaskStatus:
    return TaskStatus(
        task_id=task.id,
        status=task.status,
        stage_index=task.stage_index,
        stages=task.stages,
        progress=task.progress,
        meeting_id=task.meeting_id,
        error=task.error,
        notes=task.notes,
    )


def load_task(settings: Settings, task_id: str) -> TaskState | None:
    conn = connect(settings.db_path)
    try:
        return TaskRepo(conn).get(task_id)
    finally:
        conn.close()


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
    task = load_task(settings, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return to_status(task)


@router.get("/tasks/{task_id}/events")
def task_events(
    task_id: str, settings: Settings = Depends(get_settings)
) -> StreamingResponse:
    def stage_event(task: TaskState, index: int) -> str:
        total = len(task.stages) or 1
        return sse_event(
            "stage",
            {
                "index": index,
                "label": task.stages[index] if index < len(task.stages) else "",
                "progress": round((index + 1) / total, 4),
            },
        )

    def generator() -> Iterator[str]:
        # SSE 在独立线程里迭代，必须自己开连接（SQLite 连接不能跨线程复用）
        conn = connect(settings.db_path)
        try:
            repo = TaskRepo(conn)
            task = repo.get(task_id)
            if task is None:
                yield sse_event("error", {"message": "任务不存在"})
                return

            total = len(task.stages)
            emitted = 0
            deadline = time.monotonic() + task_timeout()
            while True:
                current = repo.get(task_id) or task
                while emitted < min(current.stage_index, total):
                    yield stage_event(current, emitted)
                    emitted += 1
                if current.status in ("done", "failed"):
                    # 收尾：把没发过的阶段补齐，前端始终看到完整流水线
                    while emitted < total:
                        yield stage_event(current, emitted)
                        emitted += 1
                    if current.status == "failed":
                        yield sse_event("error", {"message": current.error or "处理失败"})
                        return
                    yield sse_event(
                        "result", {"meeting_id": current.meeting_id, "status": current.status}
                    )
                    yield sse_event(
                        "done", {"task_id": current.id, "meeting_id": current.meeting_id}
                    )
                    return
                if time.monotonic() > deadline:
                    yield sse_event("error", {"message": "任务处理超时"})
                    return
                time.sleep(POLL_SECONDS)
        finally:
            conn.close()

    return StreamingResponse(
        generator(), media_type="text/event-stream", headers=SSE_HEADERS
    )
