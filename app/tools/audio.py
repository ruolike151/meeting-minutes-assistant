"""音频工具：process_audio 与后台任务执行（M2 T6）。

M1 的执行模型是「订阅即推进」：只有客户端在订阅 SSE 时才真正跑处理，
断开连接就没人推进任务。M2 拆成三段：

* `register_audio` 落盘 + 建任务行 + 提交后台 worker，立刻返回 task_id；
* `run_task`（后台单线程池）跑感知后端，逐阶段把进度写进 `tasks` 表；
* `GET /api/tasks/{id}/events` 只轮询任务行，把状态翻译成 SSE 事件。

用**单线程**池而不是任意并发：真实后端在 8 GB 显存上同时只应跑一个任务。
"""

from __future__ import annotations

import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.agent.minutes import generate_meeting_minutes
from app.domain.models import Meeting, Minutes
from app.perception.base import PerceptionBackend
from app.perception.factory import get_backend, get_stages
from app.perception.offline import title_from_filename
from app.store.db import connect
from app.store.files import save_audio
from app.store.repos import MeetingRepo, new_id, utcnow
from app.store.tasks import TaskState
from app.store.tasks_repo import TaskRepo

EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ma-perception")

STAGE_DELAY_ENV = "MA_STAGE_DELAY"
DEFAULT_STAGE_DELAY = 0.12


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


def stage_delay() -> float:
    """阶段之间的展示间隔（秒）。

    离线后端是瞬时完成的，没有间隔的话前端进度条会一闪而过；
    真实后端的进度由模型耗时决定，不加任何人为延迟。
    """
    try:
        return max(0.0, float(os.environ.get(STAGE_DELAY_ENV, DEFAULT_STAGE_DELAY)))
    except (TypeError, ValueError):
        return DEFAULT_STAGE_DELAY


def placeholder_minutes(backend: PerceptionBackend, meeting_id: str) -> Minutes:
    """纪要兜底（M2 遗留，M3 起只在没有模型时使用）。

    M3 起正路径是 `generate_meeting_minutes`（LLM 生成），这里退居兜底：

    * 离线后端自带固定脚本的纪要（它的语料写死，占位内容就是标准答案）；
    * 真实后端没有内容可给，返回结构合法但内容为空的纪要，
      界面显示"纪要待生成"。

    没有模型（默认的 `MA_LLM_MODE=auto` 且无 Key）时整条链路仍然可用，
    这是约束 C3 在音频路径上的落点。
    """
    provided = backend.placeholder_minutes(meeting_id)
    return provided if provided is not None else Minutes(meeting_id=meeting_id)


def register_audio(settings: Settings, filename: str, data: bytes) -> TaskState:
    """保存上传文件、建任务并提交后台执行。"""
    path = save_audio(data, filename, settings.audio_dir)
    conn = connect(settings.db_path)
    try:
        task = TaskRepo(conn).create(
            filename=path.name, path=path, size=len(data), stages=get_stages(settings)
        )
    finally:
        conn.close()
    submit_task(settings, task.id)
    return task


def process_audio(
    settings: Settings,
    payload: ProcessAudioInput,
    conn: sqlite3.Connection | None = None,
) -> ProcessAudioOutput:
    """工具层入口：为一段已落盘的音频建任务并提交执行。

    `conn` 由工具注册表传入（ToolContext），这样工具在编排层里也能直接建任务。
    """
    own_connection = conn is None
    connection = conn if conn is not None else connect(settings.db_path)
    try:
        task = TaskRepo(connection).create(
            filename=payload.filename,
            path=Path(settings.audio_dir) / payload.filename,
            size=0,
            stages=get_stages(settings),
        )
    finally:
        if own_connection:
            connection.close()
    submit_task(settings, task.id)
    return ProcessAudioOutput(
        task_id=task.id, filename=task.filename, size=task.size, stages=task.stages
    )


def submit_task(settings: Settings, task_id: str) -> None:
    """把任务交给后台 worker；接口层因此可以立刻返回。"""
    EXECUTOR.submit(run_task, settings, task_id)


def run_task(settings: Settings, task_id: str) -> None:
    """后台执行：一个任务一条连接，失败写进任务行而不是抛给调用方。"""
    conn = connect(settings.db_path)
    try:
        repo = TaskRepo(conn)
        task = repo.get(task_id)
        if task is None:
            return
        repo.mark_running(task_id)
        try:
            # 必须带上连接：本地后端要靠它加载声纹库，否则说话人全是未识别
            backend = get_backend(settings, conn)
            delay = stage_delay() if backend.name == "offline" else 0.0
            now = utcnow()
            meeting, segments = backend.process(
                task.path,
                meeting_id=new_id("m"),
                title=title_from_filename(task.filename),
                time_text=now.astimezone().strftime("%H:%M"),
                progress=_progress_writer(repo, task_id, delay),
            )
            minutes, notes = generate_meeting_minutes(
                settings,
                conn,
                meeting,
                segments,
                fallback=placeholder_minutes(backend, meeting.id),
            )
            if notes:
                repo.append_note(task_id, "；".join(notes))
            MeetingRepo(conn).create(meeting, segments, minutes)
            _index_vectors(settings, conn, meeting.id)
            repo.mark_done(task_id, meeting.id, stage_index=len(task.stages))
        except Exception as error:  # noqa: BLE001 - 任务失败要落库，而不是抛给后台线程
            repo.mark_failed(task_id, "%s: %s" % (type(error).__name__, error))
    finally:
        conn.close()


def _index_vectors(settings: Settings, conn: sqlite3.Connection, meeting_id: str) -> None:
    """给新会议建向量索引（失败不影响任务结果，只是检索退化到关键词）。"""
    from app.retrieval.encoder import EncoderUnavailable, select_encoder
    from app.retrieval.index import ensure_meeting_index

    try:
        encoder = select_encoder(settings).encoder
    except EncoderUnavailable:
        return
    try:
        ensure_meeting_index(conn, encoder, meeting_id)
    except Exception:  # noqa: BLE001 - 索引是增强，不该让任务失败
        return


def _progress_writer(repo: TaskRepo, task_id: str, delay: float):
    """把后端的阶段回调翻译成任务状态更新（外加可选的演示间隔）。"""

    def report(index: int, label: str) -> None:
        repo.update_stage(task_id, index)
        if delay:
            time.sleep(delay)

    return report
