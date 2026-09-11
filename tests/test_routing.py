"""路由测试（约束 C1：意图路由必须可断言）。"""

from __future__ import annotations

import pytest

from app.agent.routing import resolve_scope, route
from app.store.repos import MeetingRepo


@pytest.fixture()
def meetings(conn):
    return MeetingRepo(conn).list()


@pytest.mark.parametrize(
    "question, mode, intent",
    [
        ("官网改版周会定了什么？", "decisions", "query_content"),
        ("这场周会通过了吗？", "decisions", "query_content"),
        ("这三场会有什么风险？", "risks", "query_content"),
        ("有什么还没定下来的？", "open_questions", "query_content"),
        ("还有哪些待办？", "actions", "query_content"),
        ("谁提到了活动弹窗的问题？", "attribution", "query_content"),
        ("王强一共答应了哪些事？", "speaker_tasks", "query_speaker"),
        ("陈静说了什么？", "speaker_speech", "query_speaker"),
        ("公司食堂今天中午吃什么？", "content", "query_content"),
    ],
)
def test_route_table(meetings, question: str, mode: str, intent: str) -> None:
    result = route(question, meetings=meetings)
    assert result.answer_mode == mode
    assert result.intent == intent
    assert result.reason.startswith("rule:")


def test_route_summary_without_scope_asks_back(meetings) -> None:
    result = route("总结一下这几场会", meetings=meetings)
    assert result.answer_mode == "clarify"
    assert len(result.scope.meeting_ids) == 3


def test_route_summary_with_named_meeting_does_not_clarify(meetings) -> None:
    result = route("总结一下官网改版周会", meetings=meetings)
    assert result.answer_mode != "clarify"
    assert result.scope.meeting_ids == ["m_20260911"]
    assert result.scope.narrowed is True


def test_scope_prefers_named_meeting_over_active(meetings) -> None:
    scope = resolve_scope("移动端体验专项讨论有哪些风险", meetings, "m_20260911")
    assert scope.meeting_ids == ["m_20260904"]
    assert scope.source == "explicit"


def test_scope_falls_back_to_active_meeting(meetings) -> None:
    scope = resolve_scope("有什么风险", meetings, "m_20260909")
    assert scope.meeting_ids == ["m_20260909"]
    assert scope.source == "active"
    assert scope.narrowed is False


def test_scope_falls_back_to_whole_library(meetings) -> None:
    scope = resolve_scope("有什么风险", meetings, None)
    assert len(scope.meeting_ids) == 3
    assert scope.source == "library"


def test_scope_marks_ambiguous_when_two_meetings_named(meetings) -> None:
    scope = resolve_scope("官网改版周会和移动端体验专项讨论都说了什么", meetings, None)
    assert scope.ambiguous is True
    assert len(scope.meeting_ids) == 2


def test_speaker_detection_returns_id(meetings) -> None:
    result = route("王强这周做了什么", meetings=meetings)
    assert result.speaker_id == "p_wang"
    assert result.speaker_name == "王强"
