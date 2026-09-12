"""转写层：片段粒度纯函数 + 依赖缺失时的报错路径。

这些用例不需要 faster-whisper、不需要 GPU、不需要音频文件（约束 C3）。
"""

from __future__ import annotations

import sys

import pytest

from app.perception.base import BackendUnavailable
from app.perception.transcribe import (
    FasterWhisperTranscriber,
    join_text,
    logprob_to_confidence,
    merge_short_segments,
    normalize_segments,
    split_long_segments,
)
from app.perception.base import RawSegment


def seg(start: float, end: float, text: str, confidence: float = 1.0) -> RawSegment:
    return RawSegment(start=start, end=end, text=text, confidence=confidence)


def test_join_text_keeps_chinese_tight_and_spaces_latin() -> None:
    assert join_text("今天", "开会") == "今天开会"
    assert join_text("hello", "world") == "hello world"
    assert join_text("", "x") == "x"


def test_logprob_to_confidence_is_clamped() -> None:
    assert logprob_to_confidence(0.0) == pytest.approx(1.0)
    assert 0.0 <= logprob_to_confidence(-5.0) <= 1.0
    assert logprob_to_confidence(None) == 1.0


def test_merge_short_segments_joins_fragments() -> None:
    merged = merge_short_segments(
        [seg(0.0, 0.4, "你好"), seg(0.4, 1.5, "今天开个短会")], min_seconds=1.2
    )
    assert len(merged) == 1
    assert merged[0].text == "你好今天开个短会"
    assert merged[0].start == 0.0 and merged[0].end == 1.5


def test_merge_short_segments_keeps_long_segments_apart() -> None:
    merged = merge_short_segments([seg(0.0, 5.0, "第一段"), seg(5.0, 11.0, "第二段")])
    assert [item.text for item in merged] == ["第一段", "第二段"]


def test_split_long_segments_covers_the_span_and_keeps_text() -> None:
    parts = split_long_segments([seg(0.0, 45.0, "开会" * 30)], max_seconds=25.0)
    assert len(parts) >= 2
    assert all(part.duration <= 25.0 + 1e-6 for part in parts)
    assert parts[0].start == 0.0
    assert parts[-1].end == 45.0
    assert "".join(part.text for part in parts) == "开会" * 30


def test_normalize_segments_drops_empty_text() -> None:
    result = normalize_segments([seg(0.0, 3.0, "  "), seg(3.0, 8.0, "有内容")])
    assert [item.text for item in result] == ["有内容"]


def test_resolve_model_prefers_local_directory(tmp_path) -> None:
    local = tmp_path / "faster-whisper-small"
    local.mkdir()
    transcriber = FasterWhisperTranscriber(model="small", model_dir=tmp_path)
    assert transcriber.resolve_model() == str(local)


def test_resolve_model_falls_back_to_spec_name(tmp_path) -> None:
    transcriber = FasterWhisperTranscriber(model="medium", model_dir=tmp_path)
    assert transcriber.resolve_model() == "medium"


def test_missing_dependency_raises_backend_unavailable(monkeypatch, tmp_path) -> None:
    """没装 faster-whisper 时要抛 BackendUnavailable（工厂据此回落离线后端）。"""
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    transcriber = FasterWhisperTranscriber(model="small", model_dir=tmp_path)
    with pytest.raises(BackendUnavailable) as error:
        transcriber.load()
    assert "requirements-perception" in str(error.value)