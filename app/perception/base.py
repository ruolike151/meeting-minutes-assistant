"""感知层契约（《技术方案》3、14.1）。

感知层对外只暴露两样东西：

1. `PerceptionBackend` 协议 —— 上层（工具层）只依赖它，不关心下面是固定数据、
   本地模型还是云端服务；
2. 数据契约 —— `RawSegment`（转写输出）与 `SpeakerTurn`（声纹输出）。

为什么 M2 一开始就要固定协议：约束 C3 要求「无 GPU、无模型 Key 也要跑通全部测试」。
`offline.py` 与 `local.py` 实现同一个协议，契约测试对两者跑同一组断言，
于是「接入真实模型」不会把测试基线带走。

三类对象的分工：

* `RawSegment`：转写器的原始输出，只有时间戳与文本，没有身份；
* `SpeakerTurn`：声纹匹配的结果，回答「谁在什么时候说话」；
* `domain.Segment`：两者经 `align` 合成后的入库形态，带 `speaker_id` 与置信度。

一处对计划文档的显式偏差：`process()` 收的是**原始音频路径**而不是标准化后的音频。
原因是离线后端必须能在没有 ffmpeg 的机器上跑通（约束 C3），
标准化因此下移到各后端内部，由需要它的 local 后端自己调用 `audio_io.normalize()`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Protocol

from app.domain.models import Meeting, Minutes, Segment


class PerceptionError(RuntimeError):
    """感知层异常基类。"""


class BackendUnavailable(PerceptionError):
    """依赖或权重缺失（没装库、没有模型目录、没有 ffmpeg）。

    上层据此回落到离线后端，而不是让整场会议处理失败。
    """


@dataclass(frozen=True)
class NormalizedAudio:
    """标准化音频：16kHz 单声道 wav，外加从源文件带过来的元信息。"""

    path: Path
    duration: float
    sample_rate: int = 16000
    channels: int = 1
    source_path: Path | None = None


@dataclass(frozen=True)
class RawSegment:
    """转写片段：只有时间戳与文本，说话人由声纹匹配补上。"""

    start: float
    end: float
    text: str
    confidence: float = 1.0

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class SpeakerTurn:
    """一段「谁在说话」。`speaker_id` 为 None 表示未识别。"""

    start: float
    end: float
    speaker_id: str | None
    confidence: float = 0.0
    status: str = "accepted"          # accepted | needs_review
    scores: dict[str, float] = field(default_factory=dict)


# 进度回调：接口层用它推进 SSE 的阶段进度（索引, 阶段名）
ProgressCallback = Callable[[int, str], None]


class PerceptionBackend(Protocol):
    """所有感知后端的共同契约。"""

    name: str

    def stages(self) -> list[str]:
        """本后端的处理阶段名，用于任务进度展示。"""

    def transcribe(self, audio_path: str | Path) -> list[RawSegment]:
        """音频 -> 带时间戳的文本片段。"""

    def identify_speakers(
        self, audio_path: str | Path, segments: list[RawSegment]
    ) -> list[SpeakerTurn]:
        """转写片段 -> 带说话人与置信度的时间段。"""

    def placeholder_minutes(self, meeting_id: str) -> Minutes | None:
        """M2 过渡期能给出的占位纪要；返回 None 表示交给上层用空纪要兜底。

        M3 接入 LLM 后，真实纪要由编排层生成，这里一律返回 None。
        """

    def process(
        self,
        audio_path: str | Path,
        *,
        meeting_id: str,
        title: str,
        today: date | None = None,
        time_text: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> tuple[Meeting, list[Segment]]:
        """完整跑一遍：转写 -> 声纹 -> 身份对齐 -> 落库前的领域对象。"""