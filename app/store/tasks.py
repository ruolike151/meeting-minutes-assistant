"""音频任务登记表（M1 为进程内实现）。

M1 要求「上传 -> 阶段进度 -> 生成纪要」这条链路能在离线后端跑通，
任务状态先放进程内存即可；M2 接真实转写后换成带持久化的任务表，
接口形状（POST /api/audio、GET /api/tasks/{id}/events）不变。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

TaskStatus = Literal["queued", "running", "done", "failed"]


@dataclass
class TaskState:
    id: str
    filename: str
    path: Path
    size: int
    stages: list[str] = field(default_factory=list)
    status: TaskStatus = "queued"
    stage_index: int = 0
    meeting_id: str | None = None
    error: str | None = None

    @property
    def progress(self) -> float:
        if self.status == "done":
            return 1.0
        if not self.stages:
            return 0.0
        return round(self.stage_index / len(self.stages), 4)


class TaskRegistry:
    """最小可用的任务表。测试可以直接 clear() 复位。"""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}

    def create(self, *, filename: str, path: Path, size: int, stages: list[str]) -> TaskState:
        task_id = "t_" + uuid.uuid4().hex[:12]
        task = TaskState(
            id=task_id, filename=filename, path=Path(path), size=size, stages=list(stages)
        )
        self._tasks[task_id] = task
        return task

    def get(self, task_id: str) -> TaskState | None:
        return self._tasks.get(task_id)

    def all(self) -> list[TaskState]:
        return list(self._tasks.values())

    def clear(self) -> None:
        self._tasks.clear()


registry = TaskRegistry()
