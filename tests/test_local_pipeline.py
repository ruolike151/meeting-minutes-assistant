"""本地后端管线（M2 T5）：假转写 + 假编码器，不需要 GPU 与真实模型。

真实模型的冒烟测试在 `scripts/smoke_perception.py`（需 MA_RUN_PERCEPTION_SMOKE=1），
这里验证的是**管线本身**：窗口网格、声纹匹配、片段投票、参会人聚合。

依赖 numpy 与 soundfile 才能读 wav；没装时整文件跳过（约束 C3）。
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy", reason="需要 numpy 读取波形")
pytest.importorskip("soundfile", reason="需要 soundfile 读取波形")

from app.perception.base import RawSegment                      # noqa: E402
from app.perception.local import LocalPerception                 # noqa: E402
from app.perception.voiceref import build_profile                # noqa: E402


def write_two_speaker_wav(path: Path, *, rate: int = 16000) -> Path:
    """0-5 秒"大声"（说话人 A），5-10 秒"小声"（说话人 B）。

    用振幅区分说话人，假编码器据此给出不同向量——
    这样测试不需要任何声学模型，却能真实覆盖滑窗与投票。
    """
    frames = bytearray()
    for index in range(rate * 10):
        amplitude = 0.9 if index < rate * 5 else 0.2
        value = int(amplitude * 12000 * math.sin(2 * math.pi * 220 * index / rate))
        frames += struct.pack("<h", value)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return path


class FakeEncoder:
    name = "fake_encoder"
    dim = 2

    def encode(self, samples, sample_rate):
        if len(samples) < int(0.5 * sample_rate):
            return None
        # read_wav_mono 只在峰值超过 1.0 时才缩放，所以这里用绝对振幅判断：
        # 「大声」段（0.9 振幅 -> 约 0.33）判成 spk_a，「小声」段（0.2 -> 约 0.07）判成 spk_b。
        peak = float(np.max(np.abs(samples)))
        return [1.0, 0.0] if peak > 0.1 else [0.0, 1.0]

    def metadata(self):
        return {"name": self.name, "dim": self.dim}


class FakeTranscriber:
    def __init__(self, segments):
        self._segments = segments

    def transcribe(self, audio_path):
        return list(self._segments)


def build_backend(settings, segments):
    profiles = {
        "spk_a": build_profile("spk_a", [[1.0, 0.0]]),
        "spk_b": build_profile("spk_b", [[0.0, 1.0]]),
    }
    return LocalPerception(
        settings=settings,
        transcriber=FakeTranscriber(segments),
        encoder=FakeEncoder(),
        profiles=profiles,
        speaker_names={"spk_a": ("项目经理", "负责人"), "spk_b": ("运营专员", None)},
    )


def test_local_pipeline_assigns_speakers_by_window_matching(settings, tmp_path) -> None:
    audio = write_two_speaker_wav(tmp_path / "meeting.wav")
    backend = build_backend(
        settings,
        [RawSegment(start=1.0, end=4.0, text="先同步进度"), RawSegment(start=6.0, end=9.0, text="需求已经整理完")],
    )

    meeting, segments = backend.process(audio, meeting_id="m_local", title="本地管线")

    assert [segment.speaker_id for segment in segments] == ["spk_a", "spk_b"]
    assert all(segment.speaker_confidence > 0 for segment in segments)
    assert [(p.id, p.name) for p in meeting.participants] == [
        ("spk_a", "项目经理"),
        ("spk_b", "运营专员"),
    ]
    assert meeting.duration == pytest.approx(10.0, abs=0.2)
    assert meeting.audio_file == "meeting.wav"


def test_local_pipeline_without_voiceprints_marks_speaker_unknown(settings, tmp_path) -> None:
    """声纹库为空时不猜身份：片段说话人为空，参会人列表也为空。"""
    audio = write_two_speaker_wav(tmp_path / "meeting.wav")
    backend = LocalPerception(
        settings=settings,
        transcriber=FakeTranscriber([RawSegment(start=1.0, end=4.0, text="先同步进度")]),
        encoder=FakeEncoder(),
        profiles={},
        speaker_names={},
    )

    meeting, segments = backend.process(audio, meeting_id="m_unknown", title="空声纹库")

    assert [segment.speaker_id for segment in segments] == [None]
    assert meeting.participants == []