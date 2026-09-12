"""运行配置。

三类配置，全部可用环境变量覆盖（测试据此把数据写进临时目录）：

1. 运行目录：数据 / 音频 / 导出 / 静态资源 / 模型权重；
2. 检索层旋钮（《技术方案》9.1，M1 落地半段，M3 补齐向量与融合）；
3. 感知层后端与模型参数（M2）。

约束 C3 的落点在这一层：`perception_backend` 默认是 `offline`，
所以「没装模型、没有 GPU、没有网络」的机器照样能启动、能跑全部测试；
只有显式配置 `MA_PERCEPTION_BACKEND=local` 才会启用真实模型。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 环境变量统一用 MA_ 前缀（meeting assistant），避免与系统变量冲突
ENV_DATA_DIR = "MA_DATA_DIR"

DB_FILENAME = "meeting_assistant.db"

# -- 检索层参数（《技术方案》9.1）---------------------------------------
# M1 只落地「过滤 -> 关键词召回 -> 阈值拒答 -> 相邻片段组装」这半段；
# 向量召回、RRF 融合、重排属于 M3，这里先把其余旋钮的默认值固定下来。
RECALL_K1 = 50              # 关键词召回条数上限
TOP_K = 8                   # 进入上下文的片段条数
NEIGHBOR_WINDOW = 1         # 相邻片段窗口正负 1
TOKEN_BUDGET_CHARS = 2400   # 上下文预算（按字符近似，M3 换 token 计数）
MIN_COVERAGE = 0.34         # 查询词覆盖率低于该值视为证据不足，直接拒答

# 索引与查询共用的分词参数
MIN_TOKEN_LEN = 2           # 单字不成词，过滤掉「吃」「说」这类噪声

# -- 感知层参数（M2）---------------------------------------------------
DEFAULT_PERCEPTION_BACKEND = "offline"          # offline | local | auto
DEFAULT_WHISPER_MODEL = "small"                 # tiny | small | medium | 本地目录
DEFAULT_WHISPER_LANGUAGE = "zh"
DEFAULT_SPEAKER_ENCODER = "speechbrain_ecapa"   # speechbrain_ecapa | mfcc
DEFAULT_SPEAKER_MODEL_DIR = "speechbrain-spkrec-ecapa-voxceleb"
DEFAULT_SPEAKER_WINDOW = 2.5                    # 声纹匹配窗口（秒）
DEFAULT_SPEAKER_HOP = 1.25                      # 滑窗步长（秒）
DEFAULT_SPEAKER_TOPK = 3                        # 每人取相似度最高的 k 个窗口求均值
DEFAULT_SPEAKER_MIN_SCORE = -1.0                # 最低余弦相似度（低于则判为待复核）
DEFAULT_SPEAKER_MIN_MARGIN = 0.005              # 最高分与次高分之差的下限
DEFAULT_SPEAKER_ACCEPT_RATIO = 0.5              # 一个说话人轮次里 accepted 窗口的最低占比

# huggingface.co 在部分网络下不可达，权重走镜像（模型下载脚本同样使用）
HF_MIRROR_ENDPOINT = "https://hf-mirror.com"


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """一次进程运行需要的全部路径与参数。"""

    data_dir: Path
    db_path: Path
    audio_dir: Path
    export_dir: Path
    web_dir: Path
    seed_on_start: bool = True

    # -- 感知层（M2）--
    perception_backend: str = DEFAULT_PERCEPTION_BACKEND
    model_dir: Path = ROOT / "model"
    whisper_model: str = DEFAULT_WHISPER_MODEL
    whisper_device: str = "auto"          # auto | cuda | cpu
    whisper_compute: str = "auto"         # auto | float16 | int8_float16 | int8
    whisper_language: str = DEFAULT_WHISPER_LANGUAGE
    speaker_encoder: str = DEFAULT_SPEAKER_ENCODER
    speaker_model_dir: str = DEFAULT_SPEAKER_MODEL_DIR
    speaker_window: float = DEFAULT_SPEAKER_WINDOW
    speaker_hop: float = DEFAULT_SPEAKER_HOP
    speaker_topk: int = DEFAULT_SPEAKER_TOPK
    speaker_min_score: float = DEFAULT_SPEAKER_MIN_SCORE
    speaker_min_margin: float = DEFAULT_SPEAKER_MIN_MARGIN
    speaker_accept_ratio: float = DEFAULT_SPEAKER_ACCEPT_RATIO
    strict_perception: bool = False       # True 时 local 不可用直接报错，不静默回落

    @classmethod
    def from_env(
        cls,
        data_dir: str | os.PathLike[str] | None = None,
        *,
        seed_on_start: bool = True,
        web_dir: str | os.PathLike[str] | None = None,
    ) -> "Settings":
        raw = data_dir or os.environ.get(ENV_DATA_DIR) or (ROOT / "data")
        base = Path(raw).expanduser().absolute()
        model_dir = Path(_env("MA_MODEL_DIR", str(ROOT / "model"))).expanduser()
        return cls(
            data_dir=base,
            db_path=base / DB_FILENAME,
            audio_dir=base / "audio",
            export_dir=base / "export",
            web_dir=Path(web_dir).absolute() if web_dir else ROOT / "web",
            seed_on_start=seed_on_start,
            perception_backend=_env("MA_PERCEPTION_BACKEND", DEFAULT_PERCEPTION_BACKEND).lower(),
            model_dir=model_dir,
            whisper_model=_env("MA_WHISPER_MODEL", DEFAULT_WHISPER_MODEL),
            whisper_device=_env("MA_WHISPER_DEVICE", "auto").lower(),
            whisper_compute=_env("MA_WHISPER_COMPUTE", "auto").lower(),
            whisper_language=_env("MA_WHISPER_LANGUAGE", DEFAULT_WHISPER_LANGUAGE),
            speaker_encoder=_env("MA_SPEAKER_ENCODER", DEFAULT_SPEAKER_ENCODER).lower(),
            speaker_model_dir=_env("MA_SPEAKER_MODEL_DIR", DEFAULT_SPEAKER_MODEL_DIR),
            speaker_window=_env_float("MA_SPEAKER_WINDOW", DEFAULT_SPEAKER_WINDOW),
            speaker_hop=_env_float("MA_SPEAKER_HOP", DEFAULT_SPEAKER_HOP),
            speaker_topk=_env_int("MA_SPEAKER_TOPK", DEFAULT_SPEAKER_TOPK),
            speaker_min_score=_env_float("MA_SPEAKER_MIN_SCORE", DEFAULT_SPEAKER_MIN_SCORE),
            speaker_min_margin=_env_float("MA_SPEAKER_MIN_MARGIN", DEFAULT_SPEAKER_MIN_MARGIN),
            speaker_accept_ratio=_env_float(
                "MA_SPEAKER_ACCEPT_RATIO", DEFAULT_SPEAKER_ACCEPT_RATIO
            ),
            strict_perception=_env_flag("MA_PERCEPTION_STRICT", False),
        )

    # -- 派生路径 ---------------------------------------------------------

    @property
    def perception_work_dir(self) -> Path:
        """标准化音频与中间产物的落盘目录（不进 git，随 data_dir 走）。"""
        return self.data_dir / "perception"

    @property
    def resolved_model_dir(self) -> Path:
        """模型权重根目录：绝对路径原样使用，相对路径按项目根解析。"""
        return self.model_dir if self.model_dir.is_absolute() else ROOT / self.model_dir

    @property
    def resolved_speaker_model_dir(self) -> Path:
        raw = Path(self.speaker_model_dir)
        return raw if raw.is_absolute() else self.resolved_model_dir / raw

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.audio_dir, self.export_dir):
            path.mkdir(parents=True, exist_ok=True)