"""成本与延迟（M5 T7）：trace 的延迟、会议关联与汇总口径。"""

from __future__ import annotations

from app.agent.pipeline import respond
from app.config import Settings
from app.eval.metrics import aggregate_cost
from app.store.db import MIGRATIONS, schema_version
from app.store.traces_repo import TraceRepo


def test_migration_v5_adds_latency_and_eval_runs(conn) -> None:
    assert schema_version(conn) == MIGRATIONS[-1][0] >= 5
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(chat_traces)").fetchall()
    }
    assert {"latency_ms", "meeting_id", "cached"} <= columns
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "eval_runs" in tables


def test_trace_records_latency_and_meeting(conn, settings: Settings) -> None:
    respond(conn, "官网改版周会定了什么？", settings=settings)
    trace = TraceRepo(conn).list_recent(limit=1)[0]
    assert trace.latency_ms > 0, "延迟 = 各节点耗时之和，必须写进 trace"
    assert trace.meeting_id == "m_20260911", "范围唯一时要能归到具体会议"
    assert trace.cached is False, "离线运行时没有模型调用，不应记为缓存命中"


def test_cross_meeting_question_has_no_single_meeting(conn, settings: Settings) -> None:
    respond(conn, "这三场会有什么风险？", settings=settings)
    trace = TraceRepo(conn).list_recent(limit=1)[0]
    assert trace.meeting_id is None, "跨会议问答没有单一归属，不能硬塞一个会议"
    assert trace.latency_ms > 0


def test_list_by_meeting_filters(conn, settings: Settings) -> None:
    respond(conn, "官网改版周会定了什么？", settings=settings)
    respond(conn, "移动端体验专项讨论有哪些风险", settings=settings)
    repo = TraceRepo(conn)
    assert len(repo.list_by_meeting("m_20260911")) == 1
    assert len(repo.list_by_meeting("m_20260904")) == 1
    assert repo.list_by_meeting("m_nope") == []


def test_aggregate_cost_on_real_traces(conn, settings: Settings) -> None:
    respond(conn, "官网改版周会定了什么？", settings=settings)
    respond(conn, "会员体系二期需求评审的决议是什么", settings=settings)
    summary = aggregate_cost(TraceRepo(conn).list_recent(limit=10))
    assert summary["rounds"] == 2
    assert summary["p95_latency_ms"] > 0
    assert 0.0 <= summary["cache_hit_rate"] <= 1.0
