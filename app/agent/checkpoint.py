"""检查点（saver）工厂与生命周期（M4 T0）。

分层纪律：全项目只有两个模块允许 import LangGraph——

* `app/agent/graph.py`：装配 `StateGraph`（什么时候调用谁）；
* `app/agent/checkpoint.py`（本文件）：构造 saver（状态存在哪儿）。

节点层（`app/agent/nodes/`）只吃 `(state, context)`，因此换框架、换 saver
都不会波及业务逻辑与测试。

三种后端（`MA_CHECKPOINT_BACKEND`）：

* `sqlite`（默认）：`SqliteSaver`，落 `<data_dir>/checkpoints.sqlite`。
  挂起发生在后台 worker 线程、恢复发生在后续 HTTP 请求，两者之间没有共享内存，
  所以必须落盘。
* `memory`：`InMemorySaver`，只给测试与"不关心恢复"的临时运行。
* `off`：不挂起（`build_minutes_graph` 会走顺序执行），用于 M5 消融与排障。

检查点库与业务库分开：前者是框架 schema（随 LangGraph 版本走），
后者是我们的业务 schema（`PRAGMA user_version` 管），混在一起升级时会互相牵制。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.config import Settings

# 同一个路径复用同一个 saver：LangGraph 的 SQLite saver 持有连接，
# 每个请求新建连接会在测试里迅速堆积文件句柄。
_SAVERS: dict[str, Any] = {}

# 会出现在图状态里的模型。显式登记（而不是允许一切）：
# LangGraph 对未登记类型会先告警、后续版本直接拒绝反序列化，
# 在这里列清楚既消掉告警，也把"检查点里能放什么"变成一条可审查的清单。
# 教训：chat 图的状态里有 Answer / Message / Draft / ChatResult，
# 只登记 minutes 图那几个模型时它们会被静默拦掉（恢复时值变 None）。
_DOMAIN_MODELS = (
    "Meeting",
    "Participant",
    "Segment",
    "Minutes",
    "SummaryTopic",
    "Decision",
    "ActionItem",
    "OpenQuestion",
    "Risk",
    # chat 图用到的
    "Answer",
    "AnswerBlock",
    "AnswerItem",
    "Citation",
    "Message",
    "TraceStep",
)

_AGENT_MODELS = (
    ("app.agent.answers", "Draft"),
    ("app.agent.persist", "ChatResult"),
)


def checkpoint_config(thread_id: str | None) -> dict:
    """LangGraph 的调用配置：thread_id 是检查点的键。"""
    if not thread_id:
        return {}
    return {"configurable": {"thread_id": thread_id}}


def open_checkpointer(settings: Settings | None) -> Any | None:
    """按配置返回 saver；`off` 与无配置返回 None（等价于 M3 的无状态行为）。"""
    if settings is None:
        return None
    backend = (settings.checkpoint_backend or "off").lower()
    if backend == "off":
        return None
    if backend == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        # 内存 saver 不按路径共享：每个进程一份，测试里也更容易隔离
        return InMemorySaver()
    return _sqlite_saver(_ensure_parent(settings.resolved_checkpoint_path))


def _ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _sqlite_saver(path: Path) -> Any:
    key = str(path)
    cached = _SAVERS.get(key)
    if cached is not None:
        return cached
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    conn = sqlite3.connect(key, check_same_thread=False)
    serde = JsonPlusSerializer(
        allowed_msgpack_modules=[("app.domain.models", name) for name in _DOMAIN_MODELS]
        + list(_AGENT_MODELS)
    )
    saver = SqliteSaver(conn, serde=serde)
    saver.setup()
    _SAVERS[key] = saver
    return saver


def reset_cache() -> None:
    """清空 saver 缓存（测试用：换数据目录后必须重新打开）。"""
    for saver in _SAVERS.values():
        conn = getattr(saver, "conn", None)
        try:
            if conn is not None:
                conn.close()
        except Exception:  # noqa: BLE001 - 关闭失败不影响测试
            pass
    _SAVERS.clear()


__all__ = ["checkpoint_config", "open_checkpointer", "reset_cache"]
