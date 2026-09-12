"""任务状态仓储（M2 T6）。

为什么把任务落库而不是继续放内存：

* **断流不再中断处理。** M1 是「订阅即推进」，浏览器一关，转写就停了；
* **可恢复、可观测。** 任务进度、失败原因、产物会议 ID 都能查；
* **多线程安全。** 每个线程（请求线程 / worker 线程）各开一条连接，
  状态一致性靠数据库而不是共享字典。

阶段名以 JSON 数组存一列：它是展示用的字符串列表，不值得单开一张表。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from app.store.repos import iso, utcnow
from app.store.tasks import TaskState, TaskStatus


def new_task_id() -> str:
    return "t_" + uuid.uuid4().hex[:12]


def _to_task(row: sqlite3.Row) -> TaskState:
    try:
        stages = json.loads(row["stages"] or "[]")
    except json.JSONDecodeError:
        stages = []
    return TaskState(
        id=row["id"],
        filename=row["filename"],
        path=Path(row["path"]),
        size=int(row["size"] or 0),
        stages=list(stages),
        status=row["status"],
        stage_index=int(row["stage_index"] or 0),
        meeting_id=row["meeting_id"],
        error=row["error"],
    )


class TaskRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- 写 ---------------------------------------------------------

    def create(
        self,
        *,
        filename: str,
        path: str | Path,
        size: int,
        stages: list[str],
        status: TaskStatus = "queued",
        task_id: str | None = None,
    ) -> TaskState:
        task = TaskState(
            id=task_id or new_task_id(),
            filename=filename,
            path=Path(path),
            size=int(size),
            stages=list(stages),
            status=status,
        )
        now = iso(utcnow())
        self.conn.execute(
            "INSERT INTO tasks(id, filename, path, size, status, stage_index, stages,"
            " meeting_id, error, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                task.id,
                task.filename,
                str(task.path),
                task.size,
                task.status,
                task.stage_index,
                json.dumps(task.stages, ensure_ascii=False),
                task.meeting_id,
                task.error,
                now,
                now,
            ),
        )
        self.conn.commit()
        return task

    def update_stage(self, task_id: str, stage_index: int, status: TaskStatus = "running") -> None:
        self.conn.execute(
            "UPDATE tasks SET stage_index = ?, status = ?, updated_at = ? WHERE id = ?",
            (int(stage_index), status, iso(utcnow()), task_id),
        )
        self.conn.commit()

    def mark_running(self, task_id: str) -> None:
        self.conn.execute(
            "UPDATE tasks SET status = 'running', error = NULL, updated_at = ? WHERE id = ?",
            (iso(utcnow()), task_id),
        )
        self.conn.commit()

    def mark_done(self, task_id: str, meeting_id: str, *, stage_index: int) -> None:
        self.conn.execute(
            "UPDATE tasks SET status = 'done', meeting_id = ?, stage_index = ?,"
            " error = NULL, updated_at = ? WHERE id = ?",
            (meeting_id, int(stage_index), iso(utcnow()), task_id),
        )
        self.conn.commit()

    def mark_failed(self, task_id: str, message: str) -> None:
        self.conn.execute(
            "UPDATE tasks SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
            (message, iso(utcnow()), task_id),
        )
        self.conn.commit()

    # -- 读 ---------------------------------------------------------

    def get(self, task_id: str) -> TaskState | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _to_task(row) if row else None

    def list(self) -> list[TaskState]:
        rows = self.conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [_to_task(row) for row in rows]