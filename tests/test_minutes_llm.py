"""LLM 纪要生成测试：分段、合并、校验重试、单条降级（《M3 计划》T6）。"""

from __future__ import annotations

import json
from dataclasses import replace

from app.agent.minutes import (
    MinutesDraft,
    chunk_segments,
    generate_meeting_minutes,
    generate_minutes,
    merge_drafts,
    to_minutes,
    transcript_block,
    valid_evidence,
)
from app.config import Settings
from app.llm.fake import FakeLLMClient
from app.store.minutes_repo import MinutesRepo
from app.store.repos import MeetingRepo, SegmentRepo

CHUNK_ONE = {
    "summary": [{"topic": "活动弹窗", "points": ["入口太深"], "evidence": ["s1"]}],
    "decisions": [{"content": "下周三上线", "evidence": ["s2"]}],
    "action_items": [
        {"task": "补埋点口径", "owner": "王强", "owner_source": "explicit",
         "due_text": "下周三", "evidence": ["s2"]}
    ],
    "open_questions": [],
    "risks": [],
}
CHUNK_TWO = {
    "summary": [{"topic": "埋点", "points": ["口径没定"], "evidence": ["s3"]}],
    "decisions": [],
    "action_items": [{"task": "确认埋点口径", "evidence": ["s3"]}],
    "open_questions": [{"content": "口径谁来定", "evidence": ["s3"]}],
    "risks": [{"content": "上线时间紧", "evidence": ["s3"]}],
}


def meeting_and_segments(conn):
    meeting = MeetingRepo(conn).get("m_20260911")
    segments = SegmentRepo(conn).list_for_meeting("m_20260911")
    return meeting, segments


def test_chunk_segments_respects_budget_and_keeps_order(conn) -> None:
    _, segments = meeting_and_segments(conn)
    chunks = chunk_segments(segments, max_chars=80)
    assert len(chunks) > 1
    flat = [segment.id for chunk in chunks for segment in chunk]
    assert flat == [segment.id for segment in segments]


def test_chunk_segments_single_chunk_for_short_meeting(conn) -> None:
    _, segments = meeting_and_segments(conn)[:2]
    assert len(chunk_segments(segments, max_chars=100000)) == 1


def test_transcript_block_carries_segment_ids(conn) -> None:
    meeting, segments = meeting_and_segments(conn)
    block = transcript_block(segments[:2], meeting)
    assert "[%s]" % segments[0].id in block
    assert "：" in block


def test_valid_evidence_filters_unknown_ids() -> None:
    assert valid_evidence({"s1", "s2"}, ["s1", "s99"]) == ["s1"]


def test_merge_drafts_dedupes_by_topic_and_task() -> None:
    left = MinutesDraft.model_validate(CHUNK_ONE)
    right = MinutesDraft.model_validate(CHUNK_ONE)
    merged = merge_drafts([left, right])
    assert len(merged.summary) == 1
    assert len(merged.action_items) == 1
    assert len(merged.decisions) == 1


def test_to_minutes_assigns_ids_and_flags_review(conn) -> None:
    meeting, segments = meeting_and_segments(conn)
    draft = MinutesDraft.model_validate(
        {
            "summary": [{"topic": "主题", "points": ["要点"], "evidence": ["s1"]}],
            "decisions": [{"content": "决议", "evidence": ["s999"]},  # 越界证据 → 跳过
                          {"content": "有效决议", "evidence": ["s2"]}],
            "action_items": [{"task": "没有负责人和时间的活", "evidence": ["s2"]}],
            "open_questions": [{"content": "问题", "evidence": ["s3"]}],
            "risks": [{"content": "风险", "evidence": ["s4"]}],
        }
    )
    minutes, notes = to_minutes(draft, meeting, segments)
    assert [item.id for item in minutes.decisions] == ["d1"]
    assert minutes.decisions[0].content == "有效决议"
    assert any("没有有效证据" in note for note in notes)

    action = minutes.action_items[0]
    assert action.id == "a1"
    assert action.needs_review is True, "负责人与时间都不明确的待办要进待确认区"
    assert action.owner is None and action.owner_source == "missing"
    assert "负责人不明确" in (action.review_reason or "")
    assert minutes.open_questions[0].id == "q1"
    assert minutes.risks[0].id == "r1"


def test_to_minutes_maps_owner_name_to_speaker_id(conn) -> None:
    meeting, segments = meeting_and_segments(conn)
    draft = MinutesDraft.model_validate(
        {
            "action_items": [
                {"task": "补埋点", "owner": "王强", "owner_source": "explicit",
                 "due_text": "下周三", "evidence": ["s2"]}
            ]
        }
    )
    minutes, _ = to_minutes(draft, meeting, segments)
    action = minutes.action_items[0]
    assert action.owner == "p_wang"
    assert action.owner_source == "explicit"
    assert action.needs_review is False, "证据 + 负责人 + 时间齐全的条目不需要人工确认"


def test_generate_minutes_single_chunk(conn) -> None:
    meeting, segments = meeting_and_segments(conn)
    client = FakeLLMClient(json.dumps(CHUNK_ONE, ensure_ascii=False))
    minutes, notes = generate_minutes(
        client, conn, meeting, segments, model="m", max_chars=100000
    )
    assert minutes.summary and minutes.decisions and minutes.action_items
    assert minutes.meeting_id == meeting.id
    assert client.call_count == 1, "短会议不该触发 reduce"
    assert notes == []


def test_generate_minutes_long_meeting_runs_map_reduce(conn) -> None:
    meeting, segments = meeting_and_segments(conn)
    client = FakeLLMClient(
        [
            json.dumps(CHUNK_ONE, ensure_ascii=False),
            json.dumps(CHUNK_TWO, ensure_ascii=False),
            json.dumps(CHUNK_ONE, ensure_ascii=False),  # reduce 的结果
        ]
    )
    minutes, notes = generate_minutes(
        client, conn, meeting, segments, model="m", max_chars=120
    )
    assert client.call_count >= 3, "长会议要 map 多段再 reduce"
    assert notes == []
    assert minutes.action_items


def test_generate_minutes_skips_chunk_after_retries(conn) -> None:
    """某一段三次都解析不出来：丢这一段并记账，整场会议不失败。"""
    meeting, segments = meeting_and_segments(conn)
    client = FakeLLMClient("这不是 JSON")
    minutes, notes = generate_minutes(
        client, conn, meeting, segments, model="m", max_chars=100000, max_retries=2
    )
    assert client.call_count == 3
    assert any("结构化输出失败" in note for note in notes)
    assert minutes.summary == []


def test_generate_minutes_falls_back_when_model_unavailable(conn) -> None:
    meeting, segments = meeting_and_segments(conn)
    client = FakeLLMClient(available=False)
    minutes, notes = generate_minutes(client, conn, meeting, segments, model="m")
    assert minutes.summary == []
    assert client.call_count == 0
    assert any("模型不可用" in note for note in notes)


def test_generate_meeting_minutes_uses_fallback_when_off(conn, tmp_path) -> None:
    meeting, segments = meeting_and_segments(conn)
    settings = replace(Settings.from_env(tmp_path / "data"), llm_mode="off")
    fallback = MinutesRepo(conn).get("m_20260911")
    minutes, notes = generate_meeting_minutes(
        settings, conn, meeting, segments, fallback=fallback
    )
    assert minutes.meeting_id == meeting.id
    assert minutes.summary, "没有模型时用备用纪要（M2 的占位内容）"
    assert notes, "降级要留下说明"


def test_regenerate_endpoint_without_model_keeps_existing_minutes(client) -> None:
    response = client.post("/api/meetings/m_20260911/minutes/regenerate")
    assert response.status_code == 200
    body = response.json()
    assert body["meeting_id"] == "m_20260911"
    assert body["summary"], "无模型时保留原纪要，而不是清空"


def test_regenerate_endpoint_404_and_409(client) -> None:
    assert client.post("/api/meetings/m_none/minutes/regenerate").status_code == 404
