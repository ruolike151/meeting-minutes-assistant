"""声纹注册：注册音频 -> 多枚声纹向量（M2 T5）。

注册质量直接决定识别质量，因此这里做了两件事：

1. 用与会议识别**完全相同的滑窗参数**切注册音频（同一套口径才不会分布漂移）；
2. 音频太短或全是静音时，退回整段编码；仍拿不到向量就明确报错，
   而不是存一条空声纹进库。

参考实现的经验值：单人录音 15 秒以上可用，30-60 秒、环境接近会议现场更稳。
"""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.perception import audio_io
from app.perception.base import PerceptionError
from app.perception.embedding import SpeakerEncoder, create_encoder
from app.store.speakers_repo import SpeakerRecord, SpeakerRepo


def encode_speaker_audio(
    settings: Settings,
    audio_path: str | Path,
    *,
    encoder: SpeakerEncoder | None = None,
) -> list[list[float]]:
    """把一段注册音频编码成多枚声纹向量。"""
    active_encoder = encoder or create_encoder(settings)
    audio = audio_io.normalize(audio_path, settings.perception_work_dir / "enroll")
    samples, sample_rate = audio_io.read_wav_mono(audio.path)
    duration = len(samples) / sample_rate if sample_rate else 0.0

    vectors: list[list[float]] = []
    for start, end in audio_io.window_spans(
        duration, settings.speaker_window, settings.speaker_hop
    ):
        vector = active_encoder.encode(
            audio_io.slice_samples(samples, sample_rate, start, end), sample_rate
        )
        if vector:
            vectors.append(vector)

    if not vectors:
        # 音频短于一个窗口：整段编码一次，总比注册失败好
        whole = active_encoder.encode(samples, sample_rate)
        if whole:
            vectors.append(whole)

    if not vectors:
        raise PerceptionError(
            "没能从 %s 提取出声纹向量（音频太短或几乎全是静音？）" % audio_path
        )
    return vectors


def enroll_speaker(
    settings: Settings,
    conn,
    *,
    name: str,
    audio_path: str | Path,
    role: str | None = None,
    encoder: SpeakerEncoder | None = None,
) -> SpeakerRecord:
    """注册（或更新）一个发言人的声纹，返回落库记录。"""
    active_encoder = encoder or create_encoder(settings)
    vectors = encode_speaker_audio(settings, audio_path, encoder=active_encoder)
    return SpeakerRepo(conn).upsert(
        name=name, vectors=vectors, role=role, encoder=active_encoder.name
    )