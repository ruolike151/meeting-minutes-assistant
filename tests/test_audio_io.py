"""音频标准化：wav 直通、缺 ffmpeg 的报错文案、有 ffmpeg 时的真实重采样。

CI 机器不保证装了 ffmpeg，因此只有最后一个用例依赖它（用 skipif 守住 C3）。
"""

from __future__ import annotations

import shutil
import wave
from pathlib import Path

import pytest

from app.perception import audio_io
from app.perception.base import BackendUnavailable, PerceptionError


def write_wav(path: Path, *, seconds: float = 1.0, rate: int = 16000, channels: int = 1) -> Path:
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames * channels)
    return path


@pytest.fixture()
def no_ffmpeg(monkeypatch):
    monkeypatch.setattr(audio_io.shutil, "which", lambda name: None)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PerceptionError):
        audio_io.normalize(tmp_path / "nope.mp3", tmp_path / "work")


def test_directory_is_rejected(tmp_path: Path) -> None:
    folder = tmp_path / "folder.mp3"
    folder.mkdir()
    with pytest.raises(PerceptionError):
        audio_io.normalize(folder, tmp_path / "work")


def test_unsupported_suffix_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    with pytest.raises(PerceptionError):
        audio_io.normalize(target, tmp_path / "work")


def test_wav_passthrough_without_ffmpeg(tmp_path: Path, no_ffmpeg) -> None:
    source = write_wav(tmp_path / "meeting.wav", seconds=1.0)
    audio = audio_io.normalize(source, tmp_path / "work")
    assert audio.path == source, "已是 16k 单声道 wav 时应当直通，不依赖 ffmpeg"
    assert audio.sample_rate == 16000
    assert audio.channels == 1
    assert audio.duration == pytest.approx(1.0, abs=0.01)


def test_non_wav_without_ffmpeg_raises(tmp_path: Path, no_ffmpeg) -> None:
    source = tmp_path / "meeting.mp3"
    source.write_bytes(b"not-a-real-mp3")
    with pytest.raises(BackendUnavailable):
        audio_io.normalize(source, tmp_path / "work")


def test_wrong_sample_rate_without_ffmpeg_raises(tmp_path: Path, no_ffmpeg) -> None:
    source = write_wav(tmp_path / "meeting.wav", seconds=0.5, rate=8000)
    with pytest.raises(BackendUnavailable):
        audio_io.normalize(source, tmp_path / "work")


def test_probe_duration_falls_back_to_wave(tmp_path: Path, no_ffmpeg) -> None:
    source = write_wav(tmp_path / "meeting.wav", seconds=2.5)
    assert audio_io.probe_duration(source) == pytest.approx(2.5, abs=0.01)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="本机未安装 ffmpeg")
def test_ffmpeg_resamples_to_16k_mono(tmp_path: Path) -> None:
    source = write_wav(tmp_path / "meeting.wav", seconds=0.5, rate=8000)
    audio = audio_io.normalize(source, tmp_path / "work")
    assert audio.path != source
    assert audio.path.exists()
    with wave.open(str(audio.path), "rb") as handle:
        assert handle.getframerate() == 16000
        assert handle.getnchannels() == 1
    assert audio.duration == pytest.approx(0.5, abs=0.05)

def test_window_spans_cuts_evenly_and_drops_short_tail() -> None:
    spans = audio_io.window_spans(5.0, 2.5, 1.25)
    assert spans[0] == (0.0, 2.5)
    assert spans[1] == (1.25, 3.75)
    assert all(end - start >= 0.8 for start, end in spans)
    assert spans[-1][1] <= 5.0


def test_window_spans_handles_audio_shorter_than_min_window() -> None:
    assert audio_io.window_spans(0.5, 2.5, 1.25) == []


def test_window_spans_rejects_bad_parameters() -> None:
    with pytest.raises(ValueError):
        audio_io.window_spans(5.0, 0.0, 1.0)