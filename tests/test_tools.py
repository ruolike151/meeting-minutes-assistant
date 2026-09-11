"""工具层测试：每个工具都能脱离框架与模型单独调用（约束 C3）。"""

from __future__ import annotations

import pytest

from app.store.minutes_repo import MinutesRepo, OverrideRepo
from app.tools.meetings import GetMinutesInput, ListMeetingsInput, UpdateActionItemInput
from app.tools.registry import ToolContext, call_tool, get_tool, tool_names, tool_schemas
from app.tools.search import FlagUncertaintyInput, QuerySpeakerInput, SearchSegmentsInput


@pytest.fixture()
def ctx(conn, settings) -> ToolContext:
    return ToolContext(conn=conn, settings=settings)


def test_registry_exposes_json_schema_for_models() -> None:
    names = tool_names()
    assert names == [
        "list_meetings",
        "get_minutes",
        "search_segments",
        "query_speaker",
        "update_action_item",
        "flag_uncertainty",
        "process_audio",
    ]
    schemas = {item["name"]: item for item in tool_schemas()}
    assert set(schemas) == set(names)
    # 模型看到的是入参 JSON Schema，这不依赖任何编排框架
    assert "query" in schemas["search_segments"]["parameters"]["properties"]


def test_registry_rejects_bad_payload(ctx) -> None:
    with pytest.raises(ValueError):
        call_tool(ctx, "search_segments", {})  # 缺必填的 query
    with pytest.raises(KeyError):
        get_tool("not_a_tool")


def test_list_meetings_tool_filters_by_participant(ctx) -> None:
    output = call_tool(ctx, "list_meetings", ListMeetingsInput(participant="李明"))
    assert [meeting.id for meeting in output.meetings] == ["m_20260911", "m_20260904"]
    summary = output.meetings[0]
    assert summary.segment_count == 12
    assert summary.action_item_count == 4
    assert summary.review_count == 1


def test_get_minutes_tool_reports_missing_meeting(ctx) -> None:
    missing = call_tool(ctx, "get_minutes", GetMinutesInput(meeting_id="m_none"))
    assert missing.found is False
    assert missing.minutes is None


def test_get_minutes_tool_returns_action_items(ctx) -> None:
    output = call_tool(ctx, "get_minutes", GetMinutesInput(meeting_id="m_20260911"))
    assert output.found is True
    assert output.minutes is not None
    assert len(output.minutes.action_items) == 4
    assert output.minutes.decisions[0].id == "d1"


def test_update_action_item_writes_override(ctx) -> None:
    output = call_tool(
        ctx,
        "update_action_item",
        UpdateActionItemInput(item_id="m_20260911:a1", status="done"),
    )
    assert output.found is True
    assert output.item is not None and output.item.status == "done"
    stored = OverrideRepo(ctx.conn).get("m_20260911", "a1")
    assert stored is not None and stored["status"] == "done"
    # 纪要正文没有被改写，人工修改只存在 override 表里
    raw = MinutesRepo(ctx.conn).get("m_20260911")
    assert raw is not None
    assert next(item for item in raw.action_items if item.id == "a1").status == "pending"


def test_update_action_item_missing_item(ctx) -> None:
    output = call_tool(
        ctx,
        "update_action_item",
        UpdateActionItemInput(item_id="m_20260911:a99", status="done"),
    )
    assert output.found is False


def test_search_segments_tool_returns_hits_with_titles(ctx) -> None:
    output = call_tool(ctx, "search_segments", SearchSegmentsInput(query="活动弹窗点击转化率"))
    assert output.refused is False
    assert output.hits[0].meeting_title == "官网改版周会"
    assert output.hits[0].speaker_name == "陈静"


def test_query_speaker_tool_aggregates(ctx) -> None:
    output = call_tool(ctx, "query_speaker", QuerySpeakerInput(speaker="王强"))
    assert output.found is True
    assert output.speaker_id == "p_wang"
    assert output.speaking_seconds == pytest.approx(1505.0)
    assert output.turn_count == 6
    assert {item.owner for item in output.action_items} == {"p_wang"}


def test_query_speaker_unknown(ctx) -> None:
    output = call_tool(ctx, "query_speaker", QuerySpeakerInput(speaker="张三"))
    assert output.found is False


def test_flag_uncertainty_moves_item_into_review(ctx) -> None:
    output = call_tool(
        ctx,
        "flag_uncertainty",
        FlagUncertaintyInput(item_id="m_20260911:a1", reason="负责人不明确"),
    )
    assert output.found is True
    items = {item.id: item for item in MinutesRepo(ctx.conn).action_items("m_20260911")}
    assert items["a1"].needs_review is True
    assert items["a1"].review_reason == "负责人不明确"


def test_process_audio_tool_creates_task(ctx) -> None:
    output = call_tool(ctx, "process_audio", {"filename": "周会录音.mp3"})
    assert output.task_id.startswith("t_")
    assert len(output.stages) == 4
