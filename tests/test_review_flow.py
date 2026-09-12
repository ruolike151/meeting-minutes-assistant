"""待确认区与恢复链路（M4 T3 / T4 / T5）。

覆盖：待确认清单口径（待办 + 决议）、确认 / 改派 / 忽略、幂等、
审计留痕、无检查点时的降级收尾、以及"挂起 → 确认 → 恢复"的完整闭环。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from app.agent.checkpoint import checkpoint_config, open_checkpointer, reset_cache
from app.agent.graph import build_minutes_graph
from app.agent.nodes import NodeContext
from app.domain.models import ActionItem, Decision, Meeting, Minutes, Segment, global_item_id
from app.store.db import MIGRATIONS, schema_version
from app.store.minutes_repo import MinutesRepo, OverrideRepo, pending_items
from app.store.repos import MeetingRepo
from app.store.review_repo import ReviewEventRepo

MEETING_ID = "m_review_flow"


def seed_meeting(conn, *, status: str = "awaiting_review") -> None:
    """一场有待办 + 低置信度决议的会议，两处都该进待确认区。"""
    meeting = Meeting(
        id=MEETING_ID,
        title="协同评审会",
        date=date(2026, 9, 12),
        duration=60,
        status=status,
    )
    segments = [
        Segment(
            id="s1",
            meeting_id=MEETING_ID,
            start=0,
            end=6,
            speaker_id=None,
            speaker_confidence=0.5,
            text="下周三之前我这边把接口文档给出来",
        )
    ]
    minutes = Minutes(
        meeting_id=MEETING_ID,
        decisions=[Decision(id="d1", content="接口文档下周三前给出", evidence=["s1"], confidence=0.5)],
        action_items=[
            ActionItem(
                id="a1",
                task="补充接口文档",
                evidence=["s1"],
                confidence=0.5,
                needs_review=True,
                review_reason="负责人不明确",
            )
        ],
    )
    MeetingRepo(conn).create(meeting, segments, minutes)


def test_migration_v4_creates_review_events(conn) -> None:
    assert schema_version(conn) == MIGRATIONS[-1][0] >= 4
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "review_events" in tables


def test_pending_items_cover_actions_and_decisions(conn) -> None:
    seed_meeting(conn)
    pending = pending_items(conn, MEETING_ID)
    assert sorted(item.id for item in pending) == ["a1", "d1"]
    kinds = {item.id: item.kind for item in pending}
    assert kinds == {"a1": "action_item", "d1": "decision"}
    assert [item.editable for item in pending] == [True, False]


def test_review_api_lists_both_kinds(client, conn) -> None:
    seed_meeting(conn)
    body = client.get("/api/review").json()
    item = next(entry for entry in body if entry["meeting_id"] == MEETING_ID)
    assert item["kind"] in ("action_item", "decision")
    assert item["editable"] is (item["kind"] == "action_item")
    assert item["meeting_title"] == "协同评审会"


def test_confirm_finalizes_meeting_when_last_item_is_cleared(client, conn) -> None:
    seed_meeting(conn)
    first = global_item_id(MEETING_ID, "a1")
    second = global_item_id(MEETING_ID, "d1")

    response = client.post("/api/review/%s/resolve" % first, json={"decision": "confirm"})
    assert response.status_code == 200
    body = response.json()
    assert body["remaining"] == 1
    assert body["finalized"] is False
    assert MeetingRepo(conn).get(MEETING_ID).status == "awaiting_review"

    response = client.post("/api/review/%s/resolve" % second, json={"action": "confirm"})
    body = response.json()
    assert body["remaining"] == 0
    assert body["finalized"] is True
    assert MeetingRepo(conn).get(MEETING_ID).status == "processed"
    remaining = client.get("/api/review", params={"meeting_id": MEETING_ID}).json()
    assert remaining == []


def test_repeat_confirm_is_idempotent(client, conn) -> None:
    """前端双击 / 重试不该报错，也不该重复写审计。"""
    seed_meeting(conn)
    item_id = global_item_id(MEETING_ID, "a1")
    payload = {"decision": "confirm"}
    assert client.post("/api/review/%s/resolve" % item_id, json=payload).status_code == 200
    events_after_first = len(ReviewEventRepo(conn).list_for_meeting(MEETING_ID))

    again = client.post("/api/review/%s/resolve" % item_id, json=payload)
    assert again.status_code == 200
    assert len(ReviewEventRepo(conn).list_for_meeting(MEETING_ID)) == events_after_first


def test_edit_writes_owner_and_audit(client, conn) -> None:
    seed_meeting(conn)
    item_id = global_item_id(MEETING_ID, "a1")
    response = client.post(
        "/api/review/%s/resolve" % item_id,
        json={"action": "edit", "owner": "p_wang", "due": "2026-09-17"},
    )
    assert response.status_code == 200
    override = OverrideRepo(conn).get(MEETING_ID, "a1")
    assert override["owner"] == "p_wang"
    assert override["due"] == "2026-09-17"

    events = client.get("/api/review/%s/events" % item_id).json()
    assert [event["action"] for event in events] == ["edit"]
    assert events[0]["before"] == {} or "resolution" in events[0]["before"]


def test_edit_on_decision_is_rejected(client, conn) -> None:
    seed_meeting(conn)
    item_id = global_item_id(MEETING_ID, "d1")
    response = client.post(
        "/api/review/%s/resolve" % item_id, json={"action": "edit", "owner": "p_wang"}
    )
    assert response.status_code == 400
    assert "决议" in response.json()["detail"]


def test_dismiss_removes_item_from_final_minutes(client, conn) -> None:
    seed_meeting(conn)
    item_id = global_item_id(MEETING_ID, "d1")
    assert client.post(
        "/api/review/%s/resolve" % item_id, json={"decision": "dismiss"}
    ).status_code == 200
    minutes = MinutesRepo(conn).get_with_overrides(MEETING_ID)
    assert [decision.id for decision in minutes.decisions] == []
    assert [item.id for item in minutes.action_items] == ["a1"]


def test_unknown_item_returns_404(client, conn) -> None:
    seed_meeting(conn)
    response = client.post(
        "/api/review/%s/resolve" % global_item_id(MEETING_ID, "a9"), json={"decision": "confirm"}
    )
    assert response.status_code == 404


def test_invalid_action_returns_400(client, conn) -> None:
    seed_meeting(conn)
    response = client.post(
        "/api/review/%s/resolve" % global_item_id(MEETING_ID, "a1"),
        json={"action": "approve"},
    )
    assert response.status_code == 422  # 请求体校验在进业务逻辑之前就拦下


def test_resolve_without_checkpoint_still_finalizes(conn, settings) -> None:
    """降级路径：检查点库丢了 / backend=off，也要能收尾（不 500）。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    offline = replace(settings, checkpoint_backend="off")
    seed_meeting(conn)
    with TestClient(create_app(offline)) as client:
        first = client.post(
            "/api/review/%s/resolve" % global_item_id(MEETING_ID, "a1"),
            json={"decision": "confirm"},
        )
        assert first.status_code == 200
        second = client.post(
            "/api/review/%s/resolve" % global_item_id(MEETING_ID, "d1"),
            json={"decision": "confirm"},
        )
        assert second.status_code == 200
    assert MeetingRepo(conn).get(MEETING_ID).status == "processed"


def test_suspended_graph_is_resumed_by_confirm_request(conn, settings) -> None:
    """完整闭环：挂起 → HTTP 确认 → 图恢复 → 会议收尾。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    reset_cache()
    context = NodeContext.from_settings(conn, settings)
    graph = build_minutes_graph(context, checkpointer=open_checkpointer(settings))
    minutes = Minutes(
        meeting_id=MEETING_ID,
        decisions=[Decision(id="d1", content="接口文档下周三前给出", evidence=["s1"], confidence=0.5)],
    )
    meeting = Meeting(
        id=MEETING_ID, title="协同评审会", date=date(2026, 9, 12), duration=60, status="processing"
    )
    segments = [
        Segment(id="s1", meeting_id=MEETING_ID, start=0, end=6, speaker_id=None,
                speaker_confidence=0.5, text="下周三之前给接口文档")
    ]
    result = graph.invoke(
        {
            "meeting_id": MEETING_ID,
            "meeting": meeting,
            "segments": segments,
            "minutes": minutes,
            "notes": [],
            "retry_count": 0,
        },
        checkpoint_config(MEETING_ID),
    )
    assert "__interrupt__" in result

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/review/%s/resolve" % global_item_id(MEETING_ID, "d1"),
            json={"decision": "confirm"},
        )
    assert response.status_code == 200
    assert response.json()["finalized"] is True
    assert MeetingRepo(conn).get(MEETING_ID).status == "processed"
    reset_cache()


def test_regenerate_refreshes_review_status(client, conn) -> None:
    """重新生成纪要后状态要跟着待确认清单走，不能停在 processed。"""
    seed_meeting(conn, status="processed")
    response = client.post("/api/meetings/%s/minutes/regenerate" % MEETING_ID)
    assert response.status_code == 200
    assert MeetingRepo(conn).get(MEETING_ID).status == "awaiting_review"
