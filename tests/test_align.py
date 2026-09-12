"""身份对齐（M2 T5）：加权投票、未识别兜底、片段编号。"""

from __future__ import annotations

import pytest

from app.perception.align import align_segments, vote_speaker_for_span
from app.perception.base import RawSegment
from app.perception.voiceref import WindowPrediction


def win(start: float, end: float, speaker: str | None, confidence: float = 0.9, status: str = "accepted") -> WindowPrediction:
    return WindowPrediction(
        start=start,
        end=end,
        speaker_id=speaker,
        score=0.8,
        margin=0.2,
        confidence=confidence,
        status=status,
        scores={speaker or "unknown": 0.8},
    )


def test_segment_inside_one_window_takes_that_speaker() -> None:
    vote = vote_speaker_for_span(1.0, 2.0, [win(0.0, 3.0, "spk_a")])
    assert vote.speaker_id == "spk_a"
    assert vote.status == "accepted"
    assert vote.confidence == pytest.approx(0.9)


def test_segment_spanning_two_speakers_goes_to_the_dominant_one() -> None:
    """重叠时长 × 置信度：覆盖更多且更确定的一方胜出。"""
    vote = vote_speaker_for_span(
        0.0, 10.0, [win(0.0, 7.0, "spk_a", 0.9), win(7.0, 10.0, "spk_b", 0.9)]
    )
    assert vote.speaker_id == "spk_a"


def test_uncertain_window_flags_review_even_when_it_wins() -> None:
    vote = vote_speaker_for_span(0.0, 5.0, [win(0.0, 5.0, "spk_a", 0.5, "needs_review")])
    assert vote.speaker_id == "spk_a"
    assert vote.status == "needs_review"
    assert vote.confidence == pytest.approx(0.5)


def test_segment_without_any_window_is_unknown() -> None:
    """没有声纹证据就不猜身份（产品文档：无证据则明说）。"""
    vote = vote_speaker_for_span(100.0, 105.0, [win(0.0, 3.0, "spk_a")])
    assert vote.speaker_id is None
    assert vote.confidence == 0.0
    assert vote.status == "needs_review"


def test_align_segments_numbers_ids_and_keeps_text() -> None:
    segments = [
        RawSegment(start=0.0, end=3.0, text="第一句"),
        RawSegment(start=3.0, end=6.0, text="第二句"),
    ]
    windows = [win(0.0, 3.0, "spk_a"), win(3.0, 6.0, "spk_b")]
    aligned = align_segments(segments, windows, meeting_id="m_test")
    assert [item.id for item in aligned] == ["s1", "s2"]
    assert [item.meeting_id for item in aligned] == ["m_test", "m_test"]
    assert [item.speaker_id for item in aligned] == ["spk_a", "spk_b"]
    assert [item.text for item in aligned] == ["第一句", "第二句"]