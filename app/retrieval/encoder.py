"""文本编码器（《M3 计划》5.3）。

三档实现，同一个 `TextEncoder` 协议，上层不感知用哪一种：

* `LocalBGEEmbedder` —— 本地 BGE-small-zh（默认语义召回），权重在 `model/`；
* `ApiEmbedder`      —— 云端 embedding（OpenAI 兼容 `/embeddings` 端点，显式启用）；
* `HashingEmbedder`  —— 纯标准库的**确定性兜底**：字符 bigram 哈希 + 有符号计数 + L2 归一化。

为什么必须有第三档：约束 C3 要求"没有权重、没有 Key、没有网络"也能跑，
而向量检索是默认路径的一部分（不像感知层可以整体关掉）。哈希编码器不是语义模型，
它只捕捉字面重合，但它是**确定性的、零依赖的、可断言的**，足以让混合检索的
召回、融合与阈值在 CI 里被真实执行，而不是被 mock 掉。

`auto` 的选择顺序是 bge → hash；**`api` 不会被自动选中**——它要走网络与计费，
必须显式配置，否则一次环境变量缺失就会把"本地可复现"悄悄变成"依赖外部服务"。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence, runtime_checkable

from app.config import (
    DEFAULT_TEXT_ENCODER,
    HASH_EMBEDDING_DIM,
    HASH_EMBEDDING_NAME,
    VALID_TEXT_ENCODERS,
    Settings,
)

# BGE-small-zh-v1.5 的输出维度（加载后会再核对一次，不一致直接报错）
BGE_DIM = 512
BGE_NAME = "bge-small-zh-v1.5"


class EncoderUnavailable(RuntimeError):
    """编码器不可用：依赖缺失、权重缺失或未配 Key。上层据此决定是否回落。"""


@runtime_checkable
class TextEncoder(Protocol):
    name: str
    dim: int

    @property
    def storage_key(self) -> str:
        """入库用的身份键：**名称 + 维度**。

        少了维度会出真问题：两个同名编码器（比如哈希兜底的 256 维与 32 维）
        会写到同一行主键上互相覆盖，旧向量还会被当成有效向量参与召回。
        """
        ...

    def encode(self, texts: Sequence[str], *, batch_size: int = 16) -> list[list[float]]:
        ...


def l2_normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(float(value) * float(value) for value in vector))
    if norm == 0:
        return [0.0 for _ in vector]
    return [float(value) / norm for value in vector]


class HashingEmbedder:
    """字符 bigram 哈希编码器：确定性、零依赖、跨进程稳定。

    用 `hashlib` 而不是内置 `hash()`：CPython 对字符串哈希做了随机化，
    用它会让同一段文本在两个进程里得到不同向量，索引与查询就对不上了。
    """

    def __init__(self, dim: int = HASH_EMBEDDING_DIM) -> None:
        if dim <= 0:
            raise ValueError("向量维度必须为正数")
        self.dim = dim
        self.name = HASH_EMBEDDING_NAME

    @property
    def storage_key(self) -> str:
        return "%s#%d" % (self.name, self.dim)

    def _buckets(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        normalized = " ".join((text or "").lower().split())
        if not normalized:
            return vector
        grams = (
            [normalized]
            if len(normalized) < 2
            else [normalized[i : i + 2] for i in range(len(normalized) - 1)]
        )
        for gram in grams:
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            vector[value % self.dim] += 1.0 if (value >> 8) & 1 else -1.0
        return vector

    def encode(self, texts: Sequence[str], *, batch_size: int = 16) -> list[list[float]]:
        return [l2_normalize(self._buckets(text)) for text in texts]


class LocalBGEEmbedder:
    """本地 BGE：懒加载 `sentence-transformers`，权重缺失时抛 `EncoderUnavailable`。"""

    def __init__(
        self, model_dir: str | Path, *, dim: int = BGE_DIM, name: str = BGE_NAME
    ) -> None:
        self.model_path = Path(model_dir)
        self.dim = dim
        self.name = name
        self._model = None

    @property
    def storage_key(self) -> str:
        return "%s#%d" % (self.name, self.dim)

    @staticmethod
    def weights_ready(model_dir: str | Path) -> bool:
        """权重就绪的判定：配置与权重文件都在（下载中断留下的空壳不算）。"""
        path = Path(model_dir)
        if not path.is_dir():
            return False
        has_config = (path / "config.json").is_file()
        has_weights = (path / "model.safetensors").is_file() or (
            path / "pytorch_model.bin"
        ).is_file()
        return has_config and has_weights

    def _load(self):
        if self._model is not None:
            return self._model
        if not self.weights_ready(self.model_path):
            raise EncoderUnavailable(
                "本地向量权重缺失：%s（用 scripts/download_models.py --embedding-only 下载）"
                % self.model_path
            )
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as error:  # pragma: no cover - 取决于运行环境
            raise EncoderUnavailable(
                "未安装 sentence-transformers（pip install -r requirements-embedding.txt）：%s"
                % error
            ) from error
        model = SentenceTransformer(str(self.model_path))
        # sentence-transformers 6.x 把方法改名了，两个名字都兼容
        getter = getattr(model, "get_embedding_dimension", None) or getattr(
            model, "get_sentence_embedding_dimension"
        )
        dim = int(getter())
        if dim != self.dim:
            raise EncoderUnavailable(
                "权重维度与预期不一致：期望 %d，实际 %d（检查 %s）"
                % (self.dim, dim, self.model_path)
            )
        self._model = model
        return model

    def encode(self, texts: Sequence[str], *, batch_size: int = 16) -> list[list[float]]:
        model = self._load()
        vectors = model.encode(list(texts), batch_size=batch_size, normalize_embeddings=True)
        return [[float(value) for value in vector] for vector in vectors]


class ApiEmbedder:
    """云端 embedding：OpenAI 兼容 `/embeddings` 端点（决策 ② 的第二条路线）。

    与 `OpenAICompatClient` 共用 base_url 与 Key，因此换厂商同样只改 `.env`。
    维度不预设，首次调用后按返回值记录（写进 `segment_embeddings.dim`）。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        dim: int = 0,
    ) -> None:
        if not api_key:
            raise EncoderUnavailable("云端 embedding 需要 MA_LLM_API_KEY")
        if not model:
            raise EncoderUnavailable("云端 embedding 需要 MA_EMBEDDING_API_MODEL")
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.dim = int(dim)
        self.name = "api:%s" % model
        self._client = None

    @property
    def storage_key(self) -> str:
        # 维度由首次调用探测；探测前用 pending 占位，不会被写进索引表
        return "api:%s#%s" % (self.model, self.dim or "pending")

    def _load(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI  # type: ignore
        except Exception as error:  # pragma: no cover
            raise EncoderUnavailable("未安装 openai SDK：%s" % error) from error
        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout)
        return self._client

    def encode(self, texts: Sequence[str], *, batch_size: int = 16) -> list[list[float]]:
        client = self._load()
        payload = [text or " " for text in texts]
        if not payload:
            return []
        response = client.embeddings.create(model=self.model, input=payload)
        vectors = [l2_normalize(item.embedding) for item in response.data]
        if vectors and not self.dim:
            self.dim = len(vectors[0])
        return vectors


class FakeEncoder:
    """测试用：按脚本返回预设向量，未登记的文本返回 `default`。

    有了它，向量召回、RRF 融合、阈值判定的测试可以完全确定，
    不依赖任何权重、网络与随机性。
    """

    def __init__(
        self,
        mapping: dict[str, Sequence[float]] | None = None,
        *,
        default: Sequence[float] | None = None,
        name: str = "fake",
        dim: int | None = None,
        transform: Callable[[str], Sequence[float]] | None = None,
    ) -> None:
        self.mapping = dict(mapping or {})
        self.transform = transform
        sample = next(iter(self.mapping.values()), None) if self.mapping else None
        if dim is None:
            dim = len(sample) if sample is not None else len(default or []) or 4
        self.dim = int(dim)
        self.name = name
        self.default = list(default) if default is not None else [0.0] * self.dim
        self.calls: list[list[str]] = []

    @property
    def storage_key(self) -> str:
        return "%s#%d" % (self.name, self.dim)

    def encode(self, texts: Sequence[str], *, batch_size: int = 16) -> list[list[float]]:
        self.calls.append(list(texts))
        vectors: list[list[float]] = []
        for text in texts:
            if text in self.mapping:
                vectors.append([float(value) for value in self.mapping[text]])
            elif self.transform is not None:
                vectors.append([float(value) for value in self.transform(text)])
            else:
                vectors.append(list(self.default))
        return vectors


@dataclass(frozen=True)
class EncoderSelection:
    encoder: TextEncoder
    requested: str
    fallback_reason: str | None = None

    @property
    def is_fallback(self) -> bool:
        return self.fallback_reason is not None

    @property
    def name(self) -> str:
        return self.encoder.name

    @property
    def storage_key(self) -> str:
        return self.encoder.storage_key


def _try_bge(settings: Settings) -> TextEncoder:
    model_dir = settings.resolved_embedding_model_dir
    if not LocalBGEEmbedder.weights_ready(model_dir):
        raise EncoderUnavailable("未找到本地 BGE 权重：%s" % model_dir)
    encoder = LocalBGEEmbedder(model_dir)
    encoder.encode(["预热"])  # 提前暴露依赖或权重问题，别等检索时才炸
    return encoder


def _try_api(settings: Settings) -> TextEncoder:
    return ApiEmbedder(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.embedding_api_model,
        timeout=settings.llm_timeout,
    )


def select_encoder(settings: Settings) -> EncoderSelection:
    """按配置选择编码器，返回选择结果与回落原因（与感知层 factory.py 同一套写法）。"""
    requested = (settings.text_encoder or DEFAULT_TEXT_ENCODER).strip().lower()
    if requested not in VALID_TEXT_ENCODERS:
        raise EncoderUnavailable(
            "MA_TEXT_ENCODER 取值非法：%s（可选：%s）"
            % (requested, " / ".join(VALID_TEXT_ENCODERS))
        )

    if requested == "hash":
        return EncoderSelection(encoder=HashingEmbedder(), requested=requested)
    if requested == "bge":
        try:
            return EncoderSelection(encoder=_try_bge(settings), requested=requested)
        except (EncoderUnavailable, ImportError, OSError) as error:
            raise EncoderUnavailable("本地 BGE 不可用：%s" % error) from error
    if requested == "api":
        try:
            encoder = _try_api(settings)
            if not encoder.dim:  # type: ignore[attr-defined]
                # 维度必须在使用前确定，否则索引键会随首调用漂移
                encoder.encode(["维度探测"])  # type: ignore[attr-defined]
            return EncoderSelection(encoder=encoder, requested=requested)
        except (EncoderUnavailable, ImportError, OSError) as error:
            raise EncoderUnavailable("云端 embedding 不可用：%s" % error) from error

    # auto：本地权重优先，缺失就落确定性兜底；api 不在自动链里
    try:
        return EncoderSelection(encoder=_try_bge(settings), requested=requested)
    except (EncoderUnavailable, ImportError, OSError) as error:
        return EncoderSelection(
            encoder=HashingEmbedder(),
            requested=requested,
            fallback_reason="本地 BGE 不可用，已回落到哈希编码器（只捕捉字面重合）：%s" % error,
        )


def get_encoder(settings: Settings) -> TextEncoder:
    """只取编码器本体；需要回落原因时用 `select_encoder`。"""
    return select_encoder(settings).encoder
