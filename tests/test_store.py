"""存储层测试：建表、种子数据、仓储与人工修改。"""

from __future__ import annotations

from app.domain.models import Message, global_item_id
from app.store.bootstrap import seed_sample_data
from app.store.conversations_repo import ConversationRepo
from app.store.db import migrate, schema_version
from app.store.minutes_repo import MinutesRepo, OverrideRepo
from app.store.repos import MeetingRepo, SegmentRepo


def test_migration_is_idempotent(conn) -> None:
    assert schema_version(conn) == 1
    assert migrate(conn) == 1
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    for table in (
        "meetings",
        "participants",
        "segments",
        "minutes",
        "conversations",
        "messages",
        "action_item_overrides",
        "llm_cache",
    ):
        assert table in tables


def test_seed_is_idempotent(conn) -> None:
    assert MeetingRepo(conn).count() == 3
    assert seed_sample_data(conn) == 0
    assert MeetingRepo(conn).count() == 3


def test_participants_carry_speaking_seconds(conn) -> None:
    meeting = MeetingRepo(conn).get("m_20260911")
    assert meeting is not None
    wang = next(p for p in meeting.participants if p.id == "p_wang")
    # 会议 1 里王强只有 s5（487 秒到 640 秒）
    assert wang.speaking_seconds == 153.0


def test_meeting_list_filters(conn) -> None:
    repo = MeetingRepo(conn)
    assert [m.id for m in repo.list()] == ["m_20260911", "m_20260909", "m_20260904"]
    assert [m.id for m in repo.list(q="会员")] == ["m_20260909"]
    assert [m.id for m in repo.list(participant="孙悦")] == ["m_20260911", "m_20260909"]
    assert [m.id for m in repo.list(date_from="2026-09-05")] == ["m_20260911", "m_20260909"]


def test_segments_are_indexed_for_fts(conn) -> None:
    segment_repo = SegmentRepo(conn)
    assert segment_repo.count() == 32
    fts_count = conn.execute("SELECT COUNT(*) FROM segments_fts").fetchone()[0]
    assert fts_count == segment_repo.count()
    row = conn.execute(
        "SELECT s.id FROM segments_fts JOIN segments s ON s.rowid = segments_fts.rowid"
        " WHERE segments_fts MATCH ?",
        ('"弹窗"',),
    ).fetchone()
    assert row["id"] == "s2"


def test_segment_get_missing_returns_none(conn) -> None:
    assert SegmentRepo(conn).get("m_20260911", "s99") is None


def test_override_status_and_confirm(conn) -> None:
    overrides = OverrideRepo(conn)
    minutes_repo = MinutesRepo(conn)
    item_id = "a4"

    overrides.upsert("m_20260911", item_id, status="done")
    items = {item.id: item for item in minutes_repo.action_items("m_20260911")}
    assert items[item_id].status == "done"
    assert items[item_id].needs_review is True  # 只改状态，不影响待确认

    overrides.upsert("m_20260911", item_id, resolution="confirmed")
    items = {item.id: item for item in minutes_repo.action_items("m_20260911")}
    assert items[item_id].needs_review is False
    assert items[item_id].status == "done"  # 两次修改互不覆盖


def test_override_dismiss_removes_item(conn) -> None:
    OverrideRepo(conn).upsert("m_20260904", "a3", resolution="dismissed")
    ids = [item.id for item in MinutesRepo(conn).action_items("m_20260904")]
    assert "a3" not in ids


def test_conversation_round_trip(conn) -> None:
    repo = ConversationRepo(conn)
    conversation = repo.create()
    repo.append(
        conversation.id,
        Message(id="msg_1", role="user", content="周会定了什么？", created_at=conversation.created_at),
    )
    loaded = repo.get(conversation.id)
    assert loaded is not None
    assert [m.content for m in loaded.messages] == ["周会定了什么？"]
    assert repo.get("c_missing") is None


def test_global_item_id_format() -> None:
    assert global_item_id("m_20260911", "a4") == "m_20260911:a4"


def test_fts_index_survives_reindex(conn) -> None:
    """重新写入同一场会议后，外部内容 FTS 索引不能残留悬空项。

    这条守着 SegmentRepo.replace_for_meeting 的删除顺序：
    先删 FTS 索引行、再删 segments 内容行，反过来会让索引查不到东西。
    """
    repo = MeetingRepo(conn)
    meeting = repo.get("m_20260911")
    assert meeting is not None
    repo.create(meeting, SegmentRepo(conn).list_for_meeting("m_20260911"))

    assert SegmentRepo(conn).count("m_20260911") == 12
    assert conn.execute("SELECT COUNT(*) FROM segments_fts").fetchone()[0] == 32
    hits = conn.execute(
        "SELECT s.id FROM segments_fts JOIN segments s ON s.rowid = segments_fts.rowid"
        " WHERE segments_fts MATCH ?",
        ('"弹窗"',),
    ).fetchall()
    assert [row["id"] for row in hits] == ["s2"]


def test_connection_created_in_one_thread_used_in_another(conn, settings) -> None:
    """FastAPI 的同步依赖与同步路由不保证落在同一线程，连接必须能跨线程使用。"""
    import threading

    from app.store.db import connect

    shared = connect(settings.db_path)
    result: dict = {}

    def worker() -> None:
        try:
            result["count"] = shared.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
        except Exception as error:  # noqa: BLE001 - 失败信息要带回去断言
            result["error"] = repr(error)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    shared.close()

    assert "error" not in result, result.get("error")
    assert result["count"] == 3