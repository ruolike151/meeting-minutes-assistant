"""minutes 图（M4 T2）：生成 → 校验 → 人工闸门 → 收尾。

覆盖三件事：
1. 闸门触发条件（有待确认条目才挂起，`MA_REVIEW_MODE=off` 不挂起）；
2. 挂起期间待确认区读得到数据（因为 generate 就写库）；
3. 真·持久化恢复：用 SqliteSaver 挂起，再用 `Command(resume=…)` 收尾。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from app.agent.checkpoint import checkpoint_config, open_checkpointer, reset_cache
from app.agent.graph import build_minutes_graph, resume_input
from app.agent.nodes import NodeContext
from app.agent.review import pending_list
from app.domain.models import Decision, Meeting, Minutes, Segment
from app.store.minutes_repo import MinutesRepo, OverrideRepo
from app.store.repos import MeetingRepo

MEETING_ID = "m_gate_test"


def build_state(*, pending: bool):
    """构造一场待处理的会议：低置信度决议 = 需要人工确认。"""
    meeting = Meeting(
        id=MEETING_ID,
        title="闸门测试会",
        date=date(2026, 9, 12),
        duration=30,
        status="processing",
    )
    segments = [
        Segment(
            id="s1",
            meeting_id=MEETING_ID,
            start=0,
            end=5,
            speaker_id=None,
            speaker_confidence=0.5,
            text="下周三之前把方案发出来",
        )
    ]
    minutes = Minutes(
        meeting_id=MEETING_ID,
        decisions=[
            Decision(
                id="d1",
                content="方案在下周三前发出",
                evidence=["s1"],
                confidence=0.50 if pending else 0.95,
            )
        ],
    )
    return {
        "meeting_id": MEETING_ID,
        "meeting": meeting,
        "segments": segments,
        "minutes": minutes,
        "notes": [],
        "retry_count": 0,
    }


def test_no_pending_items_goes_straight_to_processed(conn, settings) -> None:
    context = NodeContext.from_settings(conn, settings)
    graph = build_minutes_graph(context, checkpointer=open_checkpointer(settings))
    result = graph.invoke(build_state(pending=False), checkpoint_config(MEETING_ID))

    assert None not in result  # 图正常跑完（没有挂起）
    assert result["status"] == "processed"
    assert MeetingRepo(conn).get(MEETING_ID).status == "processed"
    assert pending_list(conn, MEETING_ID, settings=settings) == []


def test_pending_items_suspend_and_stay_queryable(conn, settings) -> None:
    """挂起是真的挂起：任务线程可以退场，待确认区照常读得到。"""
    context = NodeContext.from_settings(conn, settings)
    calls: list[dict] = []

    def fake_interrupt(payload: dict):
        calls.append(payload)
        return {"approved": True}

    context.interrupt_fn = fake_interrupt
    graph = build_minutes_graph(context, checkpointer=open_checkpointer(settings))
    graph.invoke(build_state(pending=True), checkpoint_config(MEETING_ID))

    assert len(calls) == 1
    assert calls[0]["count"] == 1
    assert calls[0]["items"][0]["kind"] == "decision"
    # 落库发生在挂起之前，所以界面此时就能读到
    assert MeetingRepo(conn).get(MEETING_ID).status == "awaiting_review"
    pending = pending_list(conn, MEETING_ID, settings=settings)
    assert [item.id for item in pending] == ["d1"]


def test_review_mode_off_never_suspends(conn, settings) -> None:
    """MA_REVIEW_MODE=off：消融开关，生成完直接收尾（M5 会用到）。"""
    offline = replace(settings, review_mode="off")
    context = NodeContext.from_settings(conn, offline)
    calls: list[dict] = []
    context.interrupt_fn = lambda payload: calls.append(payload)  # 不应被调用

    graph = build_minutes_graph(context, checkpointer=open_checkpointer(offline))
    result = graph.invoke(build_state(pending=True), checkpoint_config(MEETING_ID))

    assert calls == []
    assert result["status"] == "processed"
    assert MeetingRepo(conn).get(MEETING_ID).status == "processed"


def test_sqlite_checkpoint_survives_interrupt_and_resume(conn, settings) -> None:
    """核心用例：真挂起（interrupt）→ 人工确认 → 另一个请求里恢复并收尾。"""
    reset_cache()
    context = NodeContext.from_settings(conn, settings)
    saver = open_checkpointer(settings)
    graph = build_minutes_graph(context, checkpointer=saver)

    result = graph.invoke(build_state(pending=True), checkpoint_config(MEETING_ID))
    assert "__interrupt__" in result, "有待确认条目时必须挂起"
    assert MeetingRepo(conn).get(MEETING_ID).status == "awaiting_review"

    # 人工确认（等价于 POST /api/review/{id}/resolve 的第一步）
    OverrideRepo(conn).upsert(MEETING_ID, "d1", resolution="confirmed")
    assert pending_list(conn, MEETING_ID, settings=settings) == []

    # 另一个请求：重建图与上下文，用同一个 thread 恢复
    fresh_context = NodeContext.from_settings(conn, settings)
    resumed = build_minutes_graph(fresh_context, checkpointer=open_checkpointer(settings))
    resumed.invoke(resume_input({"meeting_id": MEETING_ID, "approved": True}),
                   checkpoint_config(MEETING_ID))

    assert MeetingRepo(conn).get(MEETING_ID).status == "processed"
    # 模型产物只读：人工确认不改写 payload（置信度还是 0.5、confirmed 仍为 False）
    raw = MinutesRepo(conn).get(MEETING_ID)
    assert raw.decisions[0].confidence == 0.5
    assert raw.decisions[0].confirmed is False
    # 合并 override 之后才是"已确认"的最终稿
    merged = MinutesRepo(conn).get_with_overrides(MEETING_ID)
    assert merged.decisions[0].confirmed is True
    assert merged.decisions[0].needs_review is False
    reset_cache()
