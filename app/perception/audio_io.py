"""音频标准化（M2；算法口径与参考实现一致，见计划文档 v2 第 4.2 节）。

所有 ASR 与声纹模型都要求 16kHz 单声道，因此入口先统一格式。

有意做了一层降级：

* 有 ffmpeg → 正常转码（mp3 / m4a / wav / mp4 都能处理）；
* 没有 ffmpeg，但输入本来就是 16kHz 单声道 wav → 直接直通；
* 没有 ffmpeg 且需要转码 → 抛 `BackendUnavailable`，由工厂回落到离线后端。

第二条是给约束 C3 用的：CI 机器不保证装了 ffmpeg，但测试仍要能跑通。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

from app.perception.base import BackendUnavailable, NormalizedAudio, PerceptionError

from typing import Any

SUPPORTED_SUFFIXES = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}

DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1


def ensure_audio_file(path: str | Path) -> Path:
    """校验输入是一个受支持的音频文件。"""
    resolved = Path(path).expanduser()
    if not resolved.exists():
        raise PerceptionError("音频文件不存在：%s" % resolved)
    if not resolved.is_file():
        raise PerceptionError("期望音频文件，实际是目录：%s" % resolved)
    if resolved.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise PerceptionError(
            "不支持的音频扩展名 %s（支持：%s）"
            % (resolved.suffix, "、".join(sorted(SUPPORTED_SUFFIXES)))
        )
    return resolved


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def ffprobe_path() -> str | None:
    return shutil.which("ffprobe")


def _tail(text: str, limit: int = 400) -> str:
    cleaned = (text or "").strip()
    return cleaned[-limit:]


def _wav_info(path: str | Path) -> tuple[int, int, int] | None:
    """返回 (采样率, 声道数, 帧数)；不是 wav 或读不出来时返回 None。"""
    try:
        with wave.open(str(path), "rb") as handle:
            return handle.getframerate(), handle.getnchannels(), handle.getnframes()
    except (wave.Error, OSError, EOFError):
        return None


def probe_duration(path: str | Path) -> float | None:
    """音频时长（秒）。优先 ffprobe，回退 stdlib wave，都没有则返回 None。"""
    source = Path(path)
    probe = ffprobe_path()
    if probe:
        completed = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(source),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode == 0:
            try:
                payload = json.loads(completed.stdout or "{}")
                duration = payload.get("format", {}).get("duration")
                if duration is not None:
                    return float(duration)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass

    info = _wav_info(source)
    if info is not None:
        rate, _, frames = info
        if rate:
            return frames / float(rate)
    return None


def normalize(
    source: str | Path,
    work_dir: str | Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
) -> NormalizedAudio:
    """把任意受支持音频转成 16kHz 单声道 wav，并返回其元信息。"""
    src = ensure_audio_file(source)
    target_dir = Path(work_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / ("%s.%dk_mono.wav" % (src.stem, sample_rate // 1000))

    ffmpeg = ffmpeg_path()
    if ffmpeg:
        completed = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(src),
                "-ac",
                str(channels),
                "-ar",
                str(sample_rate),
                "-vn",
                str(target),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            raise PerceptionError("ffmpeg 转码失败：%s" % _tail(completed.stderr))
        return NormalizedAudio(
            path=target,
            duration=probe_duration(target) or 0.0,
            sample_rate=sample_rate,
            channels=channels,
            source_path=src,
        )

    info = _wav_info(src)
    if info is None:
        raise BackendUnavailable(
            "未安装 ffmpeg，且输入不是 wav（%s），无法标准化音频" % src.suffix
        )
    rate, actual_channels, frames = info
    if rate != sample_rate or actual_channels != channels:
        raise BackendUnavailable(
            "未安装 ffmpeg，无法把音频重采样为 %d Hz 单声道（当前 %d Hz / %d 声道）"
            % (sample_rate, rate, actual_channels)
        )
    return NormalizedAudio(
        path=src,
        duration=frames / float(rate) if rate else 0.0,
        sample_rate=rate,
        channels=actual_channels,
        source_path=src,
    )

def window_spans(
    duration: float,
    window: float,
    hop: float,
    *,
    min_window: float = 0.8,
) -> list[tuple[float, float]]:
    """把 [0, duration) 按要求切成 (start, end) 列表。

    尾巴不足 `min_window` 的窗口直接丢弃：半秒碎片算出的声纹向量，
    噪声比信息多（参考实现里也是同一个 0.8 秒下限）。

    这是纯函数，不依赖 numpy，因此「滑窗切分」这一层可以完全离线单测。
    """
    if window <= 0 or hop <= 0:
        raise ValueError("窗口长度与步长必须为正数")
    spans: list[tuple[float, float]] = []
    start = 0.0
    while start < duration:
        end = min(start + window, duration)
        if end - start < min_window:
            break
        spans.append((round(start, 3), round(end, 3)))
        start += hop
    return spans


def read_wav_mono(path: str | Path) -> tuple[Any, int]:
    """读成单声道 float32 波形并做峰值归一化。

    numpy / soundfile 在这里延迟导入：不装感知依赖的机器上，
    导入 `audio_io` 本身不能失败（约束 C3）。
    """
    try:
        import numpy as np
        import soundfile as sf
    except ImportError as error:  # pragma: no cover - 依赖缺失路径
        raise BackendUnavailable("需要 numpy 与 soundfile 才能读取音频：%s" % error) from error

    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    if peak > 0:
        mono = mono / max(peak, 1.0)
    return mono.astype("float32"), int(sample_rate)


def slice_samples(samples: Any, sample_rate: int, start: float, end: float) -> Any:
    """按秒数取波形片段（越界自动裁剪）。"""
    start_index = max(0, int(round(start * sample_rate)))
    end_index = min(len(samples), int(round(end * sample_rate)))
    return samples[start_index:end_index]