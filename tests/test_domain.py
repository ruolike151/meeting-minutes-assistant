"""领域契约测试：校验规则、序列化与对外 ID。"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.domain.models import (
    ActionItem,
    Citation,
    Minutes,
    Segment,
    global_item_id,
    split_item_id,
)


def test_segment_rejects_reversed_span() -> None:
    with pytest.raises(ValidationError):
        Segment(id="s1", meeting_id="m1", start=10, end=5, text="x")


def test_segment_duration() -> None:
    segment = Segment(id="s1", meeting_id="m1", start=3, end=42.5, text="x")
    assert segment.duration == pytest.approx(39.5)


def test_action_item_without_evidence_must_be_flagged() -> None:
    """C4：没有证据的结论不允许静默进入最终稿。"""
    with pytest.raises(ValidationError):
        ActionItem(id="a1", task="交付设计稿", evidence=[], needs_review=False)


def test_action_item_low_confidence_must_be_flagged() -> None:
    with pytest.raises(ValidationError):
        ActionItem(id="a1", task="交付设计稿", evidence=["s1"], confidence=0.5)
    flagged = ActionItem(
        id="a1", task="交付设计稿", evidence=["s1"], confidence=0.5, needs_review=True
    )
    assert flagged.needs_review is True


def test_human_confirmation_lifts_the_confidence_gate() -> None:
    """人工确认过的条目不再受置信度阈值约束，但模型置信度原样保留。"""
    confirmed = ActionItem(
        id="a1",
        task="交付设计稿",
        evidence=["s1"],
        confidence=0.5,
        confirmed=True,
    )
    assert confirmed.confidence == 0.5
    assert confirmed.needs_review is False


def test_action_item_accepts_iso_due() -> None:
    item = ActionItem(
        id="a1", task="交付设计稿", evidence=["s1"], due="2026-09-16", due_text="下周三"
    )
    assert item.due == date(2026, 9, 16)
    assert item.due_text == "下周三"


def test_global_item_id_round_trip() -> None:
    assert global_item_id("m_20260911", "a1") == "m_20260911:a1"
    assert split_item_id("m_20260911:a1") == ("m_20260911", "a1")
    with pytest.raises(ValueError):
        split_item_id("a1")


def test_minutes_round_trip_json() -> None:
    minutes = Minutes(meeting_id="m1")
    payload = minutes.model_dump(mode="json")
    assert Minutes.model_validate(payload).meeting_id == "m1"


def test_citation_key_and_label() -> None:
    citation = Citation(
        meeting_id="m1",
        meeting_title="周会",
        segment_id="s3",
        speaker="李明",
        start=190,
        label="李明 03:10",
    )
    assert citation.key == "m1:s3"
    assert citation.start == 190
