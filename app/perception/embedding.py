"""声纹编码器适配器（唯一允许 import torch / speechbrain 的地方）。

两个实现：

* `EcapaEncoder`：SpeechBrain ECAPA-TDNN（VoxCeleb 预训练，192 维），M2 主路径；
* `MfccEncoder`：MFCC 统计特征兜底，不需要任何预训练权重（精度明显更低，
  与参考实现的 MFCC 兜底后端同源），用于权重缺失时仍能演示声学特征链路。

对外只有 `encode(samples, sample_rate)` 与 `metadata()`。缺依赖或权重缺失时抛
`BackendUnavailable`；"要不要回落到离线后端"是工厂的决策，不在这里做。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from app.perception.base import BackendUnavailable, PerceptionError
from app.perception.voiceref import l2_normalize

INSTALL_HINT = (
    "缺少声纹依赖。请在项目虚拟环境里执行："
    "meeting_assistant\\Scripts\\python.exe -m pip install -r requirements-perception.txt"
)

ECAPA_REQUIRED_FILES = (
    "hyperparams.yaml",
    "embedding_model.ckpt",
    "mean_var_norm_emb.ckpt",
    "classifier.ckpt",
    "label_encoder.txt",
)

MIN_SECONDS = 0.5


class SpeakerEncoder(Protocol):
    name: str
    dim: int

    def encode(self, samples: Any, sample_rate: int) -> list[float] | None:
        """一段波形 -> 归一化声纹向量；没有可用声学信息时返回 None。"""

    def metadata(self) -> dict[str, Any]:
        """给评测报告与日志用的自描述信息。"""


def resolve_torch_device(requested: str) -> str:
    if requested and requested != "auto":
        return requested
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def validate_ecapa_dir(model_dir: str | Path, *, min_ckpt_mb: float = 50.0) -> Path:
    """校验本地 ECAPA 权重目录。

    权重文件不完整（下载中断）是最常见的坑：少了 embedding_model.ckpt 也能
    加载出一个"看起来正常"的模型，但向量全是噪声，所以这里显式校验文件清单与大小。
    """
    resolved = Path(model_dir).expanduser()
    if not resolved.is_dir():
        raise BackendUnavailable("找不到声纹模型目录：%s" % resolved)
    missing = [name for name in ECAPA_REQUIRED_FILES if not (resolved / name).exists()]
    if missing:
        raise BackendUnavailable(
            "声纹模型目录缺少文件：%s（%s）" % ("、".join(missing), resolved)
        )
    checkpoint = resolved / "embedding_model.ckpt"
    size_mb = checkpoint.stat().st_size / (1024 * 1024)
    if size_mb < min_ckpt_mb:
        raise BackendUnavailable(
            "%s 只有 %.2f MB，看着像没下完（完整权重约 80 MB）" % (checkpoint.name, size_mb)
        )
    return resolved


@dataclass
class EcapaEncoder:
    """SpeechBrain ECAPA-TDNN 声纹编码器（懒加载）。"""

    model_dir: Path
    device: str = "auto"

    name = "speechbrain_ecapa"
    dim = 192

    def __post_init__(self) -> None:
        self.model_dir = validate_ecapa_dir(self.model_dir)
        self._classifier: Any = None
        self._resolved_device = resolve_torch_device(self.device)
        # SpeechBrain 要求 ``cuda:N`` 形式；只写 ``cuda`` 会退化成
        # "Could not parse CUDA device string" 警告（虽然仍然能用，但日志很脏）
        if self._resolved_device.startswith("cuda") and ":" not in self._resolved_device:
            self._resolved_device += ":0"

    def load(self) -> Any:
        if self._classifier is not None:
            return self._classifier
        try:
            from speechbrain.inference.classifiers import EncoderClassifier
            from speechbrain.utils.fetching import FetchConfig, LocalStrategy
        except ImportError as error:
            raise BackendUnavailable(INSTALL_HINT) from error

        runtime_dir = self.model_dir / ".runtime_cache"
        try:
            self._classifier = EncoderClassifier.from_hparams(
                source=str(self.model_dir),
                savedir=str(runtime_dir),
                overrides={"pretrained_path": self.model_dir.as_posix()},
                # 明确禁止联网：权重已在本地，缺文件就让上层回落，而不是悄悄下载
                local_strategy=LocalStrategy.NO_LINK,
                fetch_config=FetchConfig(allow_network=False),
                run_opts={"device": self._resolved_device},
            )
        except Exception as error:  # noqa: BLE001 - 加载失败统一算"后端不可用"
            raise BackendUnavailable("加载 ECAPA 声纹模型失败：%s" % error) from error
        return self._classifier

    def encode(self, samples: Any, sample_rate: int) -> list[float] | None:
        if samples is None or len(samples) < int(MIN_SECONDS * sample_rate):
            return None
        try:
            import numpy as np
            import torch
            import torchaudio
        except ImportError as error:
            raise BackendUnavailable(INSTALL_HINT) from error

        classifier = self.load()
        waveform = torch.from_numpy(np.asarray(samples, dtype="float32")).unsqueeze(0)
        if sample_rate != 16000:
            waveform = torchaudio.functional.resample(waveform, sample_rate, 16000)
        with torch.inference_mode():
            embedding = classifier.encode_batch(waveform, normalize=False)
        vector = embedding.squeeze().detach().cpu().numpy().astype("float32")
        return list(l2_normalize(vector.tolist()))

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "SpeechBrain ECAPA-TDNN 预训练说话人 embedding（VoxCeleb）",
            "model_dir": str(self.model_dir),
            "dim": self.dim,
            "device": self._resolved_device,
        }


@dataclass
class MfccEncoder:
    """MFCC 统计特征兜底：不需要预训练权重，精度明显更低。"""

    name = "mfcc"
    dim = 100

    def encode(self, samples: Any, sample_rate: int) -> list[float] | None:
        if samples is None or len(samples) < int(MIN_SECONDS * sample_rate):
            return None
        try:
            import numpy as np
            import torch
            import torchaudio
        except ImportError as error:
            raise BackendUnavailable(INSTALL_HINT) from error

        waveform = torch.from_numpy(np.asarray(samples, dtype="float32")).unsqueeze(0)
        frame_length = int(0.025 * sample_rate)
        hop_length = int(0.010 * sample_rate)
        transform = torchaudio.transforms.MFCC(
            sample_rate=sample_rate,
            n_mfcc=30,
            melkwargs={
                "n_fft": 512,
                "hop_length": hop_length,
                "n_mels": 48,
                "center": False,
                "power": 2.0,
            },
        )
        with torch.no_grad():
            mfcc = transform(waveform).squeeze(0).transpose(0, 1).cpu().numpy()

        rms = _frame_rms(np.asarray(samples, dtype="float32"), frame_length, hop_length)
        usable = min(len(mfcc), len(rms))
        if usable < 5:
            return None
        mfcc = mfcc[:usable]
        rms = rms[:usable]

        threshold = max(float(np.quantile(rms, 0.55)) * 0.65, float(np.max(rms)) * 0.035, 1e-5)
        voiced = rms > threshold
        if int(voiced.sum()) < 5:
            voiced = rms > max(float(np.max(rms)) * 0.02, 1e-6)
        if int(voiced.sum()) < 5:
            return None

        cepstra = mfcc[voiced][:, 1:25]
        delta = np.diff(cepstra, axis=0) if len(cepstra) > 1 else np.zeros_like(cepstra)
        voiced_rms = rms[voiced]
        features = np.concatenate(
            [
                cepstra.mean(axis=0),
                cepstra.std(axis=0),
                delta.mean(axis=0),
                delta.std(axis=0),
                np.array(
                    [
                        voiced_rms.mean(),
                        voiced_rms.std(),
                        np.percentile(voiced_rms, 25),
                        np.percentile(voiced_rms, 75),
                    ],
                    dtype="float32",
                ),
            ]
        )
        return list(l2_normalize([float(value) for value in features]))

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "MFCC 统计声学特征（无预训练权重的兜底后端）",
            "dim": self.dim,
        }


def _frame_rms(samples: Any, frame_length: int, hop_length: int) -> Any:
    import numpy as np

    if len(samples) < frame_length:
        return np.array([float(np.sqrt(np.mean(samples**2) + 1e-10))], dtype="float32")
    values = []
    for start in range(0, len(samples) - frame_length + 1, hop_length):
        frame = samples[start : start + frame_length]
        values.append(float(np.sqrt(np.mean(frame**2) + 1e-10)))
    return np.asarray(values, dtype="float32")


def create_encoder(settings: Any) -> SpeakerEncoder:
    """按配置造编码器（默认 ECAPA，权重缺失时由工厂决定是否回落）。"""
    backend = getattr(settings, "speaker_encoder", "speechbrain_ecapa")
    if backend == "mfcc":
        return MfccEncoder()
    if backend != "speechbrain_ecapa":
        raise PerceptionError("未知的声纹编码器：%s" % backend)
    return EcapaEncoder(
        model_dir=settings.resolved_speaker_model_dir,
        device=getattr(settings, "whisper_device", "auto"),
    )