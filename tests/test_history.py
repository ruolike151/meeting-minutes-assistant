"""多轮上下文测试：最近轮次、摘要压缩、范围继承（《M3 计划》T7）。"""

from __future__ import annotations

from dataclasses import replace

from app.agent.history import (
    COMPRESS_THRESHOLD,
    compress_history,
    inherit_scope,
    load_history,
    scope_marker,
)
from app.agent.pipeline import respond
from app.config import Settings
from app.domain.models import Message
from app.llm.fake import FakeLLMClient
from app.store.conversations_repo import ConversationRepo
from app.store.repos import utcnow


def seed_conversation(conn, rounds: int, *, scope: str | None = None) -> str:
    repo = ConversationRepo(conn)
    conversation = repo.create()
    for index in range(rounds):
        repo.append(
            conversation.id,
            Message(
                id="msg_u%d" % index,
                role="user",
                content="第 %d 个问题" % (index + 1),
                created_at=utcnow(),
            ),
        )
        marker = ("[scope=%s]" % scope) if scope and index == 0 else ""
        repo.append(
            conversation.id,
            Message(
                id="msg_a%d" % index,
                role="assistant",
                content="第 %d 个回答 %s" % (index + 1, marker),
                created_at=utcnow(),
            ),
        )
    return conversation.id


def test_load_history_keeps_recent_rounds_only(conn) -> None:
    conversation_id = seed_conversation(conn, 4)
    history, summary = load_history(conn, conversation_id, recent_rounds=2)
    assert len(history) == 4, "两轮 = 4 条消息"
    assert history[0].content == "第 3 个问题"
    assert summary == ""


def test_load_history_without_conversation(conn) -> None:
    assert load_history(conn, None) == ([], "")
    assert load_history(conn, "c_missing") == ([], "")


def test_scope_marker_roundtrip() -> None:
    marker = scope_marker(["m_1", "m_2"])
    assert marker == "[scope=m_1,m_2]"
    from app.llm.client import ChatMessage

    assert inherit_scope([ChatMessage(role="assistant", content="结论 %s" % marker)]) == ["m_1", "m_2"]


def test_inherit_scope_prefers_the_latest_marker() -> None:
    from app.llm.client import ChatMessage

    history = [
        ChatMessage(role="assistant", content="老的 [scope=m_old]"),
        ChatMessage(role="user", content="换个会"),
        ChatMessage(role="assistant", content="新的 [scope=m_new]"),
    ]
    assert inherit_scope(history) == ["m_new"]


def test_inherit_scope_without_marker_returns_empty() -> None:
    from app.llm.client import ChatMessage

    assert inherit_scope([ChatMessage(role="assistant", content="没有标记")]) == []


def test_compress_history_skips_short_conversation(conn) -> None:
    conversation_id = seed_conversation(conn, 2)
    client = FakeLLMClient("摘要")
    assert compress_history(client, conn, conversation_id, model="m") is None
    assert client.call_count == 0


def test_compress_history_writes_summary(conn) -> None:
    conversation_id = seed_conversation(conn, 6)  # 12 条消息 > 阈值 8
    client = FakeLLMClient("前几轮问了周会的决议与待办")
    summary = compress_history(client, conn, conversation_id, model="m")
    assert summary == "前几轮问了周会的决议与待办"
    assert client.call_count == 1
    assert ConversationRepo(conn).summary(conversation_id) == summary


def test_compress_history_skips_when_model_unavailable(conn) -> None:
    conversation_id = seed_conversation(conn, 6)
    client = FakeLLMClient("摘要", available=False)
    assert compress_history(client, conn, conversation_id, model="m") is None
    assert client.call_count == 0
    assert ConversationRepo(conn).summary(conversation_id) is None


def test_compress_threshold_is_documented_value() -> None:
    assert COMPRESS_THRESHOLD == 8


def test_scope_is_inherited_across_turns(conn) -> None:
    """追问「那这场会……」时沿用上一轮锁定的会议，不用前端再传 meeting_id。"""
    settings = replace(Settings.from_env(), text_encoder="hash")
    first = respond(
        conn, "官网改版周会定了什么？", settings=settings, active_meeting_id="m_20260911"
    )
    assert first.answer.scope_meeting_ids == ["m_20260911"]

    second = respond(
        conn,
        "那这场会还有什么风险？",
        settings=settings,
        conversation_id=first.conversation_id,
    )
    assert second.answer.scope_meeting_ids == ["m_20260911"], "范围要从会话历史继承"
    scopes = {citation.meeting_id for citation in second.answer.citations}
    assert scopes <= {"m_20260911"}


def test_new_conversation_does_not_inherit(conn) -> None:
    settings = replace(Settings.from_env(), text_encoder="hash")
    first = respond(conn, "官网改版周会定了什么？", settings=settings, active_meeting_id="m_20260911")
    second = respond(conn, "那还有什么风险？", settings=settings)
    assert second.conversation_id != first.conversation_id
    assert len(second.answer.scope_meeting_ids) == 3, "新会话回到整个会议库"
