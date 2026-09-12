"""检查点（M4 T0）：saver 三态、跨连接持久、thread 隔离与分层纪律。"""

from __future__ import annotations

import sqlite3
import re
from dataclasses import replace
from pathlib import Path

from app.agent.checkpoint import checkpoint_config, open_checkpointer, reset_cache
from app.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_backend_off_returns_none(settings: Settings) -> None:
    assert open_checkpointer(replace(settings, checkpoint_backend="off")) is None
    assert open_checkpointer(None) is None


def test_memory_backend_returns_saver(settings: Settings) -> None:
    saver = open_checkpointer(replace(settings, checkpoint_backend="memory"))
    assert saver is not None
    assert hasattr(saver, "get_tuple")


def test_sqlite_saver_persists_across_connections(settings: Settings) -> None:
    """挂起在 worker 线程、恢复在后续请求：检查点必须能跨连接读回。"""
    reset_cache()
    saver = open_checkpointer(settings)
    assert saver is not None
    path = settings.resolved_checkpoint_path
    assert path.is_file()

    # 第二次调用（模拟"另一个请求"）复用同一个 saver 与其连接
    assert open_checkpointer(settings) is saver

    conn = sqlite3.connect(str(path))
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()
    assert "checkpoints" in tables
    reset_cache()


def test_checkpoint_config_uses_thread_id() -> None:
    assert checkpoint_config("c_1") == {"configurable": {"thread_id": "c_1"}}
    assert checkpoint_config(None) == {}
    assert checkpoint_config("") == {}


def test_only_two_modules_import_langgraph() -> None:
    """分层纪律：节点层不 import 框架（换掉 LangGraph 不波及业务逻辑）。"""
    allowed = {"app/agent/graph.py", "app/agent/checkpoint.py"}
    pattern = re.compile(r"^\s*(?:from|import)\s+langgraph", re.MULTILINE)
    offenders: list[str] = []
    for path in (ROOT / "app").rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        if relative in allowed:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(relative)
    assert not offenders, "只有 graph.py 与 checkpoint.py 可以 import langgraph：%s" % offenders
