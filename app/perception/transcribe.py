"""转写适配器（faster-whisper）。

为什么是 faster-whisper 而不是 WhisperX：参考实现里的 demo 并没有启用强制对齐
（`--align` 默认关闭），而本项目需要的粒度是**片段级时间戳**——faster-whisper
直接就能给。少一层封装，就少一个版本漂移源（《技术方案》16 节把「框架版本漂移」
列为明确风险）。

模型解析顺序：`MA_WHISPER_MODEL` 指到本地目录就直接用（权重落在 `model/`），
否则当作规格名交给 faster-whisper（会读 `HF_ENDPOINT` 指向的镜像下载）。

片段粒度由两个纯函数控制，它们可以脱离模型单测：
* 太短的碎片并进相邻片段（碎片检索价值低，还会拉低声纹投票权重）；
* 太长的片段按字数均分切开（一条片段横跨多个话题时证据链会失焦）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from app.perception.base import BackendUnavailable, PerceptionError, RawSegment

KNOWN_MODEL_SIZES = {
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v1", "large-v2", "large-v3",
    "large-v3-turbo", "distil-large-v3",
}

DEFAULT_MIN_SECONDS = 1.2
DEFAULT_MAX_SECONDS = 25.0

INSTALL_HINT = (
    "未安装 faster-whisper。请在项目虚拟环境里执行："
    "meeting_assistant\\Scripts\\python.exe -m pip install -r requirements-perception.txt"
)


def logprob_to_confidence(avg_logprob: float | None) -> float:
    """faster-whisper 的平均对数概率 -> 0-1 置信度。"""
    if avg_logprob is None:
        return 1.0
    return max(0.0, min(1.0, math.exp(float(avg_logprob))))


def join_text(left: str, right: str) -> str:
    """拼接相邻片段文本：中文直接接，西文之间补空格。"""
    if not left:
        return right
    if not right:
        return left
    if left[-1].isascii() and left[-1].isalnum() and right[0].isascii() and right[0].isalnum():
        return left + " " + right
    return left + right


def merge_short_segments(
    segments: Sequence[RawSegment], *, min_seconds: float = DEFAULT_MIN_SECONDS
) -> list[RawSegment]:
    """把过短的片段并进相邻片段（按时间顺序贪心合并）。"""
    merged: list[RawSegment] = []
    for segment in segments:
        if segment.end < segment.start:
            raise ValueError("片段结束时间不能早于开始时间")
        if merged and (segment.end - merged[-1].start) <= min_seconds:
            previous = merged[-1]
            merged[-1] = RawSegment(
                start=previous.start,
                end=max(previous.end, segment.end),
                text=join_text(previous.text, segment.text),
                confidence=min(previous.confidence, segment.confidence),
            )
        elif merged and merged[-1].duration < min_seconds:
            previous = merged[-1]
            merged[-1] = RawSegment(
                start=previous.start,
                end=max(previous.end, segment.end),
                text=join_text(previous.text, segment.text),
                confidence=min(previous.confidence, segment.confidence),
            )
        else:
            merged.append(segment)
    return merged


def split_long_segments(
    segments: Sequence[RawSegment], *, max_seconds: float = DEFAULT_MAX_SECONDS
) -> list[RawSegment]:
    """把超长片段按时间等分切开，文本按字数比例分配。"""
    result: list[RawSegment] = []
    for segment in segments:
        duration = segment.duration
        if duration <= max_seconds or max_seconds <= 0:
            result.append(segment)
            continue
        parts = max(2, math.ceil(duration / max_seconds))
        text = segment.text
        total_chars = max(1, len(text))
        cursor = 0
        for index in range(parts):
            start = segment.start + duration * index / parts
            end = segment.start + duration * (index + 1) / parts
            take = total_chars if index == parts - 1 else max(1, total_chars // parts)
            chunk = text[cursor : cursor + take] or text[-1:]
            cursor += take
            result.append(
                RawSegment(
                    start=round(start, 3),
                    end=round(end, 3),
                    text=chunk,
                    confidence=segment.confidence,
                )
            )
    return result


def normalize_segments(
    segments: Sequence[RawSegment],
    *,
    min_seconds: float = DEFAULT_MIN_SECONDS,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> list[RawSegment]:
    """先合并碎片再切长段，产出检索粒度合适的片段序列。"""
    cleaned = [segment for segment in segments if segment.text.strip()]
    return split_long_segments(
        merge_short_segments(cleaned, min_seconds=min_seconds), max_seconds=max_seconds
    )


@dataclass
class FasterWhisperTranscriber:
    """懒加载的 faster-whisper 转写器（模型只在第一次转写时载入）。"""

    model: str = "small"
    device: str = "auto"
    compute_type: str = "auto"
    language: str = "zh"
    model_dir: Path | None = None
    _model: Any = field(default=None, repr=False, compare=False)

    # -- 解析模型与设备 ---------------------------------------------------

    def resolve_model(self) -> str:
        """返回本地模型目录或规格名。

        先看显式路径，再看 `model_dir` 下的 `faster-whisper-<规格>` 目录，
        都没有才把规格名交给 faster-whisper 去下载。
        """
        raw = str(self.model or "").strip()
        if not raw:
            raise BackendUnavailable("MA_WHISPER_MODEL 为空，无法确定转写模型")
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            return str(candidate)
        if self.model_dir is not None:
            local = Path(self.model_dir) / ("faster-whisper-%s" % raw)
            if local.is_dir():
                return str(local)
        return raw

    def resolve_device(self) -> str:
        if self.device and self.device != "auto":
            return self.device
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    def resolve_compute_type(self, device: str) -> str:
        if self.compute_type and self.compute_type != "auto":
            return self.compute_type
        return "float16" if device.startswith("cuda") else "int8"

    # -- 执行 -------------------------------------------------------------

    def load(self) -> Any:
        """载入模型（幂等）。依赖或权重缺失时抛 `BackendUnavailable`。"""
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as error:
            raise BackendUnavailable(INSTALL_HINT) from error

        device = self.resolve_device()
        target = self.resolve_model()
        try:
            self._model = WhisperModel(
                target, device=device, compute_type=self.resolve_compute_type(device)
            )
        except Exception as error:  # noqa: BLE001 - 权重缺失/下载失败都算"后端不可用"
            raise BackendUnavailable("加载 Whisper 模型失败（%s）：%s" % (target, error)) from error
        return self._model

    def transcribe(self, audio_path: str | Path) -> list[RawSegment]:
        model = self.load()
        language = None if self.language in ("", "auto") else self.language
        try:
            raw_segments, _info = model.transcribe(
                str(audio_path), language=language, vad_filter=True, beam_size=5
            )
            collected = [
                RawSegment(
                    start=float(segment.start),
                    end=float(segment.end),
                    text=str(segment.text).strip(),
                    confidence=logprob_to_confidence(getattr(segment, "avg_logprob", None)),
                )
                for segment in raw_segments
            ]
        except Exception as error:  # noqa: BLE001 - 统一转成感知层异常
            raise PerceptionError("转写失败：%s" % error) from error
        return normalize_segments(collected)