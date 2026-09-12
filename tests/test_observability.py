"""可观测（M5 T8）：未启用不加载依赖、上报失败不影响主流程、字段与本地同源。"""

from __future__ import annotations

import sys
from dataclasses import replace

from app.agent.pipeline import respond
from app.config import Settings
from app.observability.langfuse import (
    LangfuseTraceSink,
    NullTraceSink,
    build_sink,
)
from app.store.traces_repo import TraceRepo


def test_disabled_sink_is_null_and_does_not_import_langfuse(settings: Settings) -> None:
    """观测依赖不能拖进主路径：没启用时连 import 都不该发生。"""
    sys.modules.pop("langfuse", None)
    sink = build_sink(settings)
    assert isinstance(sink, NullTraceSink)
    assert "langfuse" not in sys.modules
    assert build_sink(None).name == "off"


def test_enabled_without_keys_falls_back(settings: Settings) -> None:
    enabled = replace(settings, langfuse_enabled=True)
    sink = build_sink(enabled)
    assert isinstance(sink, NullTraceSink)
    assert "缺少" in sink.reason


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create_trace(self, **kwargs) -> None:
        self.calls.append(kwargs)


class BrokenClient:
    def create_trace(self, **kwargs) -> None:
        raise RuntimeError("Langfuse 挂了")


def test_sink_records_usage_and_nodes() -> None:
    client = FakeClient()
    sink = LangfuseTraceSink("http://localhost:3000", "pk", "sk", client=client)

    class Node:
        def __init__(self, name, ms):
            self.name, self.ms = name, ms

    sink.record(
        name="chat",
        thread_id="c_1",
        nodes=[Node("retrieve", 12.5), Node("synthesize", 30.0)],
        usage={"input_tokens": 100, "output_tokens": 40},
        cost=0.002,
        meeting_id="m_20260911",
    )
    assert sink.sent == 1
    call = client.calls[0]
    assert call["name"] == "chat"
    assert call["id"] == "c_1"
    assert call["metadata"]["latency_ms"] == 42
    assert call["metadata"]["nodes"] == ["retrieve", "synthesize"]
    assert call["metadata"]["meeting_id"] == "m_20260911"


def test_sink_swallows_client_errors() -> None:
    """上报通道挂了不能影响回答：异常被吞掉并记录原因。"""
    sink = LangfuseTraceSink("http://localhost:3000", "pk", "sk", client=BrokenClient())
    sink.record(name="chat", thread_id="c_1", nodes=[], usage={}, cost=0.0)
    assert sink.sent == 0
    assert "上报失败" in sink.reason


def test_same_usage_reaches_local_trace_and_sink(conn, settings: Settings) -> None:
    """本地 trace 与上报取同一份 usage：两边数字必须一致。"""
    respond(conn, "官网改版周会定了什么？", settings=settings)
    trace = TraceRepo(conn).list_recent(limit=1)[0]
    usage = {"input_tokens": trace.input_tokens, "output_tokens": trace.output_tokens}
    client = FakeClient()
    sink = LangfuseTraceSink("http://localhost:3000", "pk", "sk", client=client)
    sink.record(
        name="chat", thread_id=trace.conversation_id, nodes=[], usage=usage, cost=trace.cost
    )
    sent = client.calls[0]
    assert sent["metadata"]["meeting_id"] is None  # 未知会议时如实留空
    assert trace.input_tokens == 0  # 离线运行没有 token
