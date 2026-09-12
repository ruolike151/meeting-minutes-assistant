"""运行配置。

四类配置，全部可用环境变量或项目根目录的 `.env` 覆盖（测试据此把数据写进临时目录）：

1. 运行目录：数据 / 音频 / 导出 / 静态资源 / 模型权重；
2. 检索层旋钮（《技术方案》9.1，M3 起含向量召回与 RRF 融合）；
3. 感知层后端与模型参数（M2）。
4. LLM 与文本向量（M3）。

约束 C3 的落点在这一层：`perception_backend` 默认是 `offline`，
`llm_mode` 默认是 `auto`（没有 Key 就等价于 `off`），`text_encoder` 默认是 `auto`
（没有本地权重就落到哈希兜底），所以「没装模型、没有 GPU、没有网络、没有 Key」
的机器照样能启动、能跑全部测试；真实模型都是显式配置后才启用。

密钥纪律（《M3 计划》Global Constraints 第 7 条）：API Key 只从环境变量或 `.env` 读取，
不写进代码、不写进日志、不入库；`.env` 已被 `.gitignore` 排除，仓库里只提交 `.env.example`。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 环境变量统一用 MA_ 前缀（meeting assistant），避免与系统变量冲突
ENV_DATA_DIR = "MA_DATA_DIR"

DB_FILENAME = "meeting_assistant.db"


def load_project_env() -> bool:
    """把项目根目录的 `.env` 读进 `os.environ`，返回是否读到了文件。

    两条规则：
    * **真实环境变量优先**（`override=False`）：部署环境与 CI 可以直接覆盖本地文件，
      命令行临时设置的变量也不会被文件悄悄改掉；
    * **没装 python-dotenv 也不报错**：这一层是便利功能，不该成为启动前提（约束 C3）。
    """
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return False
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:  # pragma: no cover - 取决于运行环境
        return False
    load_dotenv(env_path, override=False)
    return True


load_project_env()

# -- 检索层参数（《技术方案》9.1）---------------------------------------
# M1 落地了「过滤 -> 关键词召回 -> 阈值拒答 -> 相邻片段组装」；
# M3 补上向量召回与 RRF 融合，重排（第 6 步）按决策 ③ 留到 M5。
RECALL_K1 = 50              # 关键词召回条数上限
RECALL_K2 = 50              # 向量召回条数上限
RRF_K = 60                  # RRF 融合常数：score = Σ 1 / (k + rank)
TOP_K = 8                   # 进入上下文的片段条数
NEIGHBOR_WINDOW = 1         # 相邻片段窗口正负 1
TOKEN_BUDGET_CHARS = 2400   # 上下文预算（按字符近似，M3 换 token 计数）
MIN_COVERAGE = 0.34         # 查询词覆盖率低于该值视为证据不足，直接拒答
# 向量余弦阈值：初始值 0.35 来自《技术方案》9.1，实测偏松——
# BGE-small-zh 会把"公司食堂今天中午吃什么"这类域外问题映射到 0.4~0.6 的相似度区间，
# 用标注集扫参后定为 0.50：拒答正确率 0.25 → 1.00，域内召回与 MRR 不变
# （见 docs/M3-编排与检索评测报告.md，可用 scripts/eval_retrieval.py --sweep 复现）。
MIN_COSINE = 0.50           # 向量余弦 top1 低于该值视为证据不足，直接拒答

# 拒答阈值只认两个可解释量：查询词覆盖率与向量余弦 top1。
# 原因见《M3 计划》5.2：RRF 分只用排名算出来，跨查询不可比，不能当绝对阈值用。

# 索引与查询共用的分词参数
MIN_TOKEN_LEN = 2           # 单字不成词，过滤掉「吃」「说」这类噪声

# -- LLM 与文本向量（M3）-----------------------------------------------
DEFAULT_LLM_PROVIDER = "openai"          # 协议名，不是厂商名：换厂商只改 base_url + 模型
DEFAULT_LLM_MODE = "auto"                # auto | live | cache_first | off
DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"   # DeepSeek 官方域名（/v1 也可用）
DEFAULT_LLM_MODEL = "deepseek-flash"
DEFAULT_LLM_TIMEOUT = 60.0
DEFAULT_ROUTE_MODE = "auto"              # auto | llm | rules
DEFAULT_VERIFY_MODE = "rule"             # rule | llm
DEFAULT_STRUCTURED_MODE = "json_object"  # json_object | json_schema | prompt

VALID_LLM_PROVIDERS = ("openai",)
VALID_LLM_MODES = ("auto", "live", "cache_first", "off")
VALID_ROUTE_MODES = ("auto", "llm", "rules")
VALID_VERIFY_MODES = ("rule", "llm")
VALID_STRUCTURED_MODES = ("json_object", "json_schema", "prompt")

DEFAULT_TEXT_ENCODER = "auto"            # auto | bge | api | hash
VALID_TEXT_ENCODERS = ("auto", "bge", "api", "hash")
DEFAULT_EMBEDDING_MODEL_DIR = "bge-small-zh-v1.5"
DEFAULT_EMBEDDING_BATCH_SIZE = 16
HASH_EMBEDDING_DIM = 256                 # 纯标准库兜底编码器的维度
HASH_EMBEDDING_NAME = "hash-bigram-v1"   # 记录进 segment_embeddings.encoder
DEFAULT_HISTORY_ROUNDS = 3

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


def _env_choice(name: str, default: str, valid: tuple[str, ...]) -> str:
    """取值必须落在白名单里；非法值回落默认值。

    这里不抛异常，是因为工厂层才是报错的合适位置（感知层的 factory.py 就是
    这个约定）：配置读错一个字母不该让整个进程起不来，但要能被断言到。
    """
    value = _env(name, default).strip().lower()
    return value if value in valid else default


def _env_price_pair() -> tuple[float, float] | None:
    """`MA_LLM_PRICE_INPUT` / `MA_LLM_PRICE_OUTPUT`（元 / 百万 token）。

    两个都没配就返回 None（用内置价目表）；配了但解析不出来同样返回 None，
    而不是抛错——价格只影响"花了多少"的展示，不该拦住启动。
    """
    raw_input = os.environ.get("MA_LLM_PRICE_INPUT")
    raw_output = os.environ.get("MA_LLM_PRICE_OUTPUT")
    if raw_input in (None, "") and raw_output in (None, ""):
        return None
    try:
        return (float(raw_input or 0), float(raw_output or 0))
    except ValueError:
        return None


@dataclass(frozen=True)
class RetrievalSettings:
    """检索层旋钮。

    单独成对象是为了让 `scripts/eval_retrieval.py` 能在不改环境变量的情况下
    逐组扫参（K1 / K2 / RRF k / 阈值 / 窗口），一次进程里跑完整个网格。
    """

    recall_k1: int = RECALL_K1
    recall_k2: int = RECALL_K2
    rrf_k: int = RRF_K
    top_k: int = TOP_K
    min_coverage: float = MIN_COVERAGE
    min_cosine: float = MIN_COSINE
    neighbor_window: int = NEIGHBOR_WINDOW
    token_budget: int = TOKEN_BUDGET_CHARS

    @classmethod
    def from_env(cls) -> "RetrievalSettings":
        return cls(
            recall_k1=_env_int("MA_RECALL_K1", RECALL_K1),
            recall_k2=_env_int("MA_RECALL_K2", RECALL_K2),
            rrf_k=_env_int("MA_RRF_K", RRF_K),
            top_k=_env_int("MA_RETRIEVAL_TOP_K", TOP_K),
            min_coverage=_env_float("MA_MIN_COVERAGE", MIN_COVERAGE),
            min_cosine=_env_float("MA_MIN_COSINE", MIN_COSINE),
            neighbor_window=_env_int("MA_NEIGHBOR_WINDOW", NEIGHBOR_WINDOW),
            token_budget=_env_int("MA_TOKEN_BUDGET", TOKEN_BUDGET_CHARS),
        )

    def replaced(self, **changes) -> "RetrievalSettings":
        """扫参用：返回替换了部分字段的新配置（frozen dataclass 的标准做法）。"""
        from dataclasses import replace

        return replace(self, **changes)


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

    # -- LLM（M3）--
    llm_provider: str = DEFAULT_LLM_PROVIDER      # 只认 openai 兼容协议
    llm_mode: str = DEFAULT_LLM_MODE              # auto | live | cache_first | off
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_api_key: str = ""                         # 空 = 未配置，绝不入库、绝不进日志
    llm_model_route: str = DEFAULT_LLM_MODEL
    llm_model_answer: str = DEFAULT_LLM_MODEL
    llm_model_minutes: str = DEFAULT_LLM_MODEL
    llm_timeout: float = DEFAULT_LLM_TIMEOUT
    route_mode: str = DEFAULT_ROUTE_MODE          # auto | llm | rules
    verify_mode: str = DEFAULT_VERIFY_MODE        # rule | llm
    llm_structured_mode: str = DEFAULT_STRUCTURED_MODE
    llm_price_override: tuple[float, float] | None = None

    # -- 文本向量（M3）--
    text_encoder: str = DEFAULT_TEXT_ENCODER      # auto | bge | api | hash
    embedding_model_dir: str = DEFAULT_EMBEDDING_MODEL_DIR
    embedding_api_model: str = ""                 # 走 MA_LLM_BASE_URL 的 /embeddings
    embedding_batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE

    # -- 检索与会话（M3）--
    retrieval: RetrievalSettings = field(default_factory=RetrievalSettings.from_env)
    history_rounds: int = DEFAULT_HISTORY_ROUNDS

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
            llm_provider=_env_choice(
                "MA_LLM_PROVIDER", DEFAULT_LLM_PROVIDER, VALID_LLM_PROVIDERS
            ),
            llm_mode=_env_choice("MA_LLM_MODE", DEFAULT_LLM_MODE, VALID_LLM_MODES),
            llm_base_url=_env("MA_LLM_BASE_URL", DEFAULT_LLM_BASE_URL),
            llm_api_key=_env("MA_LLM_API_KEY", ""),
            llm_model_route=_env("MA_LLM_MODEL_ROUTE", DEFAULT_LLM_MODEL),
            llm_model_answer=_env("MA_LLM_MODEL_ANSWER", DEFAULT_LLM_MODEL),
            llm_model_minutes=_env("MA_LLM_MODEL_MINUTES", DEFAULT_LLM_MODEL),
            llm_timeout=_env_float("MA_LLM_TIMEOUT", DEFAULT_LLM_TIMEOUT),
            route_mode=_env_choice("MA_ROUTE_MODE", DEFAULT_ROUTE_MODE, VALID_ROUTE_MODES),
            verify_mode=_env_choice("MA_VERIFY_MODE", DEFAULT_VERIFY_MODE, VALID_VERIFY_MODES),
            llm_structured_mode=_env_choice(
                "MA_LLM_STRUCTURED_MODE", DEFAULT_STRUCTURED_MODE, VALID_STRUCTURED_MODES
            ),
            llm_price_override=_env_price_pair(),
            text_encoder=_env_choice("MA_TEXT_ENCODER", DEFAULT_TEXT_ENCODER, VALID_TEXT_ENCODERS),
            embedding_model_dir=_env(
                "MA_EMBEDDING_MODEL_DIR",
                str(ROOT / "model" / DEFAULT_EMBEDDING_MODEL_DIR),
            ),
            embedding_api_model=_env("MA_EMBEDDING_API_MODEL", ""),
            embedding_batch_size=_env_int(
                "MA_EMBEDDING_BATCH_SIZE", DEFAULT_EMBEDDING_BATCH_SIZE
            ),
            retrieval=RetrievalSettings.from_env(),
            history_rounds=_env_int("MA_HISTORY_ROUNDS", DEFAULT_HISTORY_ROUNDS),
        )

    # -- 派生路径 ---------------------------------------------------------

    @property
    def effective_llm_mode(self) -> str:
        """把 `auto` 解析成实际模式。

        * `auto`：有 Key 就是 `live`，没有 Key 就是 `off`；
        * `live` / `cache_first` 但没配 Key：降级成 `off`——**不报错、不静默发请求**，
          真实调用失败必须有明确的可见原因，而不是抛一个用户看不懂的 401。
        """
        mode = (self.llm_mode or DEFAULT_LLM_MODE).strip().lower()
        if mode == "auto":
            return "live" if self.llm_api_key else "off"
        if mode in ("live", "cache_first") and not self.llm_api_key:
            return "off"
        return mode

    @property
    def llm_mode_reason(self) -> str:
        """降级原因，写进 trace 与冒烟脚本输出（"为什么没走真实模型"要看得见）。"""
        mode = (self.llm_mode or DEFAULT_LLM_MODE).strip().lower()
        effective = self.effective_llm_mode
        if effective == mode or mode == "auto" and effective == "live":
            return ""
        if not self.llm_api_key:
            return "未配置 MA_LLM_API_KEY，已降到 %s（规则路由 + 模板装配）" % effective
        return "MA_LLM_MODE=%s 已解析为 %s" % (mode, effective)

    @property
    def llm_available(self) -> bool:
        return self.effective_llm_mode != "off"

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

    @property
    def resolved_embedding_model_dir(self) -> Path:
        """本地文本向量权重目录：绝对路径原样使用，相对路径按项目根解析。"""
        raw = Path(self.embedding_model_dir)
        return raw if raw.is_absolute() else ROOT / raw

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.audio_dir, self.export_dir):
            path.mkdir(parents=True, exist_ok=True)
