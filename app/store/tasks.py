"""音频任务的数据结构（持久化见 `tasks_repo.py`）。

M1 的时代任务状态放在进程内存里（一个 `registry` 字典），代价有两条：
客户端断开 SSE 就没人推进任务、进程重启后任务全丢。
M2 要跑真实转写（几十秒到几分钟），这两条都会变成事故，因此状态改存 SQLite。
"""

from __future__ import annotations

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
        reached = max(0, min(self.stage_index, len(self.stages)))
        return round(reached / len(self.stages), 4)