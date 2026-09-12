"""任务执行改造（M2 T6）：后台执行、状态落库、失败路径。

M1 的任务状态放在进程内存里，本文件里最关键的一条断言是
「换一条连接仍然读得到任务」—— 它正好是 M1 做不到的那件事。
"""

from __future__ import annotations

import json
import re
import time

from app.store.db import connect
from app.store.tasks import TaskState
from app.store.tasks_repo import TaskRepo


def parse_sse(text: str) -> list[tuple[str, dict]]:
    return [
        (match.group(1), json.loads(match.group(2)))
        for match in re.finditer(r"event: (\w+)\ndata: (.*)", text)
    ]


def upload(client, name: str = "任务落库.mp3") -> str:
    response = client.post(
        "/api/audio", files={"file": (name, b"fake-audio-bytes" * 4, "audio/mpeg")}
    )
    assert response.status_code == 200
    return response.json()["task_id"]


def load(settings, task_id: str) -> TaskState | None:
    conn = connect(settings.db_path)
    try:
        return TaskRepo(conn).get(task_id)
    finally:
        conn.close()


def wait_finished(settings, task_id: str, timeout: float = 10.0) -> TaskState:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = load(settings, task_id)
        if task is not None and task.status in ("done", "failed"):
            return task
        time.sleep(0.02)
    raise AssertionError("任务未在 %.1f 秒内结束" % timeout)


def test_task_is_persisted_across_connections(client, settings) -> None:
    task_id = upload(client)
    task = load(settings, task_id)
    assert task is not None
    assert task.filename == "任务落库.mp3"
    assert task.stages, "任务要带上阶段名，前端据此渲染进度"


def test_upload_runs_without_subscribing(client, settings) -> None:
    """M1 必须有人订阅 SSE 才会处理；M2 上传即开始，订阅只是看进度。"""
    task_id = upload(client, "后台执行.mp3")
    task = wait_finished(settings, task_id)
    assert task.status == "done"
    assert task.meeting_id
    assert task.progress == 1.0


def test_events_replay_full_pipeline_for_late_subscriber(client, settings) -> None:
    task_id = upload(client, "迟到订阅.mp3")
    wait_finished(settings, task_id)
    events = parse_sse(client.get("/api/tasks/" + task_id + "/events").text)
    names = [name for name, _ in events]
    assert names.count("stage") == 4, "客户端晚连也要看到完整流水线"
    assert names[-2:] == ["result", "done"]


def test_failed_task_reports_error(client, settings, monkeypatch) -> None:
    class BoomBackend:
        name = "boom"

        def stages(self) -> list[str]:
            return ["阶段一", "阶段二"]

        def process(self, *args, **kwargs):
            raise RuntimeError("模拟感知失败")

        def placeholder_minutes(self, meeting_id: str):
            return None

    monkeypatch.setattr(
        "app.tools.audio.get_backend", lambda settings, conn=None: BoomBackend()
    )

    task_id = upload(client, "会失败.mp3")
    task = wait_finished(settings, task_id)
    assert task.status == "failed"
    assert "模拟感知失败" in (task.error or "")

    events = parse_sse(client.get("/api/tasks/" + task_id + "/events").text)
    assert [name for name, _ in events][-1] == "error"
    assert "模拟感知失败" in dict(events)["error"]["message"]

def test_worker_passes_db_connection_to_backend(client, settings, monkeypatch) -> None:
    """回归用例：worker 必须把连接传给后端。

    少了这个连接，本地后端就加载不到声纹库，结果是"转写正常、说话人全是空"——
    这个缺陷在真机验证时出现过一次，因此固化成测试。
    """
    seen: dict[str, object] = {}

    class RecordingBackend:
        name = "recording"

        def stages(self) -> list[str]:
            return ["阶段一"]

        def process(self, *args, **kwargs):
            raise RuntimeError("仅用于中断任务")

        def placeholder_minutes(self, meeting_id: str):
            return None

    def fake_get_backend(settings_arg, conn=None):
        seen["conn"] = conn
        return RecordingBackend()

    monkeypatch.setattr("app.tools.audio.get_backend", fake_get_backend)

    task_id = upload(client, "连接传递.mp3")
    wait_finished(settings, task_id)
    assert seen.get("conn") is not None, "worker 必须把数据库连接传给感知后端"
