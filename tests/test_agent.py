"""编排层测试：回答形态、证据校验、trace 与会话落库。"""

from __future__ import annotations

from app.agent.answers import AnswerContext, Draft, note_block, quote_block
from app.agent.pipeline import plain_text, respond, verify_evidence
from app.domain.models import AnswerItem, Citation
from app.store.conversations_repo import ConversationRepo
from app.store.repos import SegmentRepo


def test_respond_decisions_carries_resolvable_citations(conn) -> None:
    result = respond(conn, "官网改版周会定了什么？")
    answer = result.answer
    assert answer.answer_mode == "decisions"
    assert answer.scope_meeting_ids == ["m_20260911"]
    assert answer.citations, "决议必须带证据"
    for citation in answer.citations:
        assert SegmentRepo(conn).get(citation.meeting_id, citation.segment_id) is not None


def test_respond_refuses_without_evidence(conn) -> None:
    result = respond(conn, "公司食堂今天中午吃什么？")
    assert result.answer.answer_mode == "refuse"
    assert result.answer.citations == []
    assert any(block.kind == "note" for block in result.answer.blocks)


def test_respond_tracks_observed_nodes(conn) -> None:
    result = respond(conn, "这三场会有什么风险？")
    names = [step.name for step in result.answer.trace]
    # M3 起链路被拆成图节点，每个节点各记一步耗时（可观测粒度更细）
    assert names == ["route", "synthesize", "verify_evidence", "persist"]
    assert set(names) <= {
        "route",
        "resolve_scope",
        "clarify",
        "create_task",
        "retrieve",
        "grade",
        "refuse",
        "synthesize",
        "verify_evidence",
        "persist",
    }
    assert all(step.ms >= 0 for step in result.answer.trace)


def test_respond_persists_conversation(conn) -> None:
    first = respond(conn, "有什么还没定下来的？")
    second = respond(
        conn, "那谁负责埋点口径？", conversation_id=first.conversation_id
    )
    assert second.conversation_id == first.conversation_id
    conversation = ConversationRepo(conn).get(first.conversation_id)
    assert conversation is not None
    assert [m.role for m in conversation.messages] == ["user", "assistant", "user", "assistant"]
    assert conversation.messages[1].answer is not None
    assert conversation.messages[1].answer.answer_mode == "open_questions"


def test_verify_evidence_drops_unknown_citation(conn) -> None:
    ctx = AnswerContext(conn=conn, meetings=[])
    fabricated = Citation(
        meeting_id="m_20260911",
        meeting_title="官网改版周会",
        segment_id="s999",
        speaker="B",
        start=1,
        label="B 00:01",
    )
    draft = Draft(
        lead="测试",
        blocks=[quote_block("块", [AnswerItem(text="结论", citations=[fabricated])])],
    )
    verified, dropped, unsupported = verify_evidence(ctx, draft)
    assert dropped == 1
    assert unsupported == 1
    assert verified.blocks[0].items[0].citations == []
    assert verified.blocks[0].items[0].warn is True


def test_verify_evidence_keeps_notes(conn) -> None:
    ctx = AnswerContext(conn=conn, meetings=[])
    draft = Draft(lead="测试", blocks=[note_block("没有证据时的说明")])
    verified, dropped, unsupported = verify_evidence(ctx, draft)
    assert (dropped, unsupported) == (0, 0)
    assert verified.blocks[0].kind == "note"


def test_plain_text_includes_items(conn) -> None:
    result = respond(conn, "这三场会有什么风险？")
    text = plain_text(result.answer)
    assert result.answer.lead in text
    assert "【风险与预警】" in text
