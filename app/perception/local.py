"""本地真实感知后端（M2 主路径）。

四步流水线：

```text
① audio_io.normalize   ffmpeg -> 16kHz 单声道
② transcribe           faster-whisper -> RawSegment（片段级时间戳）
③ match_windows        滑窗 ECAPA -> 与声纹库比对 -> WindowPrediction
④ align_segments       片段 × 窗口 -> domain.Segment（说话人 + 置信度）
```

`from_settings()` 只校验「依赖与权重是否就位」，**不加载模型**（工厂会被频繁调用，
不能每次几秒钟）；模型在第一次转写/编码时才载入。

拿不到可用配置时抛 `BackendUnavailable`，由 `factory` 决定是回落离线后端
还是直接报错（`MA_PERCEPTION_STRICT=1`）。
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Mapping

from app.config import Settings
from app.domain.models import Meeting, Minutes, Participant, Segment
from app.perception import align as align_module
from app.perception import audio_io, voiceref
from app.perception.base import (
    BackendUnavailable,
    NormalizedAudio,
    PerceptionError,
    ProgressCallback,
    RawSegment,
    SpeakerTurn,
)
from app.perception.embedding import SpeakerEncoder, create_encoder
from app.perception.transcribe import (
    INSTALL_HINT,
    KNOWN_MODEL_SIZES,
    FasterWhisperTranscriber,
)
from app.perception.voiceref import VoiceProfile, WindowPrediction
from app.store.speakers_repo import SpeakerRepo

STAGES = [
    "音频标准化 · 16kHz 单声道",
    "语音转写 · faster-whisper",
    "声纹匹配 · ECAPA-TDNN",
    "身份对齐 · 片段归属",
]

REQUIRED_MODULES = ("faster_whisper", "numpy", "soundfile")


def require_dependencies() -> None:
    """检查可选依赖是否装齐；缺了就抛 BackendUnavailable（带安装命令）。"""
    missing = []
    for module in REQUIRED_MODULES:
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(module)
    if missing:
        raise BackendUnavailable("缺少感知依赖：%s。%s" % ("、".join(missing), INSTALL_HINT))


@dataclass
class LocalPerception:
    """真实模型后端：faster-whisper 转写 + ECAPA 声纹匹配。"""

    settings: Settings
    transcriber: FasterWhisperTranscriber
    encoder: SpeakerEncoder
    profiles: Mapping[str, VoiceProfile] = field(default_factory=dict)
    speaker_names: Mapping[str, tuple[str, str | None]] = field(default_factory=dict)

    name = "local"

    # -- 构造 -------------------------------------------------------------

    @classmethod
    def from_settings(cls, settings: Settings, conn=None) -> "LocalPerception":
        require_dependencies()

        transcriber = FasterWhisperTranscriber(
            model=settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute,
            language=settings.whisper_language,
            model_dir=settings.resolved_model_dir,
        )
        target = transcriber.resolve_model()
        if not Path(target).is_dir() and Path(target).name not in KNOWN_MODEL_SIZES:
            raise BackendUnavailable(
                "找不到转写模型：%s（既不是本地目录，也不是已知规格名）。"
                "可用 scripts/download_models.py 把权重下到 model/ 目录" % target
            )

        encoder = create_encoder(settings)

        profiles: dict[str, VoiceProfile] = {}
        names: dict[str, tuple[str, str | None]] = {}
        if conn is not None:
            repo = SpeakerRepo(conn)
            profiles = repo.profiles(encoder=encoder.name)
            for record in repo.all():
                if record.id in profiles:
                    names[record.id] = (record.name, record.role)

        return cls(
            settings=settings,
            transcriber=transcriber,
            encoder=encoder,
            profiles=profiles,
            speaker_names=names,
        )

    # -- 协议实现 ---------------------------------------------------------

    def stages(self) -> list[str]:
        return list(STAGES)

    def transcribe(self, audio_path: str | Path) -> list[RawSegment]:
        """转写（约定传入已标准化的音频；`process()` 内部就是这么用的）。"""
        return self.transcriber.transcribe(audio_path)

    def identify_speakers(
        self, audio_path: str | Path, segments: list[RawSegment]
    ) -> list[SpeakerTurn]:
        audio = audio_io.normalize(audio_path, self._work_dir)
        windows = self.match_windows(audio)
        return voiceref.merge_windows_to_turns(
            windows,
            max_gap=self.settings.speaker_hop,
            accept_ratio=self.settings.speaker_accept_ratio,
        )

    def placeholder_minutes(self, meeting_id: str) -> Minutes | None:
        """真实后端不产占位纪要：内容生成属于 M3 的 LLM 节点。"""
        return None

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
        audio = audio_io.normalize(audio_path, self._work_dir)
        _report(progress, 0, STAGES)

        raws = self.transcriber.transcribe(audio.path)
        _report(progress, 1, STAGES)

        windows = self.match_windows(audio)
        _report(progress, 2, STAGES)

        segments = align_module.align_segments(raws, windows, meeting_id=meeting_id)
        _report(progress, 3, STAGES)

        meeting = Meeting(
            id=meeting_id,
            title=title,
            date=today or date.today(),
            time=time_text or "00:00",
            duration=round(audio.duration, 3),
            status="processed",
            audio_file=Path(audio_path).name,
            participants=self._participants(segments),
        )
        return meeting, segments

    # -- 内部 -------------------------------------------------------------

    @property
    def _work_dir(self) -> Path:
        return self.settings.perception_work_dir

    def match_windows(self, audio: NormalizedAudio) -> list[WindowPrediction]:
        """对整段会议音频做滑窗声纹匹配（窗口网格固定，与转写片段无关）。"""
        if not self.profiles:
            # 声纹库为空：不是错误，但要说清楚为什么所有片段都没有说话人
            return []
        samples, sample_rate = audio_io.read_wav_mono(audio.path)
        duration = len(samples) / sample_rate if sample_rate else audio.duration
        predictions: list[WindowPrediction] = []
        for start, end in audio_io.window_spans(
            duration, self.settings.speaker_window, self.settings.speaker_hop
        ):
            window = audio_io.slice_samples(samples, sample_rate, start, end)
            vector = self.encoder.encode(window, sample_rate)
            if not vector:
                continue
            prediction = voiceref.match_window(
                vector,
                self.profiles,
                start=start,
                end=end,
                topk=self.settings.speaker_topk,
                min_score=self.settings.speaker_min_score,
                min_margin=self.settings.speaker_min_margin,
            )
            if prediction is not None:
                predictions.append(prediction)
        return predictions

    def _participants(self, segments: list[Segment]) -> list[Participant]:
        """参会人 = 片段里真正出现过的说话人（按首次出现排序）。"""
        participants: list[Participant] = []
        seen: set[str] = set()
        for segment in segments:
            speaker_id = segment.speaker_id
            if not speaker_id or speaker_id in seen:
                continue
            seen.add(speaker_id)
            name, role = self.speaker_names.get(speaker_id, (speaker_id, None))
            participants.append(Participant(id=speaker_id, name=name, role=role))
        return participants


def _report(progress: ProgressCallback | None, index: int, stages: list[str]) -> None:
    if progress is not None and index < len(stages):
        progress(index, stages[index])