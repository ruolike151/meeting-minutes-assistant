"""文本编码器测试：确定性哈希兜底、回落链、云端 embedding 解析（不联网）。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.config import Settings
from app.retrieval.encoder import (
    ApiEmbedder,
    EncoderUnavailable,
    FakeEncoder,
    HashingEmbedder,
    LocalBGEEmbedder,
    l2_normalize,
    select_encoder,
)


def make_settings(tmp_path, **changes) -> Settings:
    base = Settings.from_env(tmp_path / "data")
    return replace(base, **changes)


def test_hash_embedding_is_deterministic_across_instances() -> None:
    """跨实例、跨进程稳定：不能用内置 hash()（字符串哈希被随机化）。"""
    first = HashingEmbedder().encode(["退款之后积分怎么处理"])[0]
    second = HashingEmbedder().encode(["退款之后积分怎么处理"])[0]
    assert first == second


def test_hash_embedding_is_l2_normalized() -> None:
    vector = HashingEmbedder().encode(["C 负责埋点口径"])[0]
    norm = sum(value * value for value in vector) ** 0.5
    assert norm == pytest.approx(1.0)


def test_hash_embedding_separates_different_text() -> None:
    encoder = HashingEmbedder()
    left, right = encoder.encode(["退款积分规则", "食堂今天中午吃什么"])
    cosine = sum(a * b for a, b in zip(left, right))
    assert abs(cosine) < 0.5, "不相关文本不该被哈希编码器判成近似"


def test_hash_embedding_handles_empty_text() -> None:
    vector = HashingEmbedder().encode([""])[0]
    assert len(vector) == 256
    assert set(vector) == {0.0}


def test_l2_normalize_zero_vector_stays_zero() -> None:
    assert l2_normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]


def test_fake_encoder_returns_scripted_vectors() -> None:
    encoder = FakeEncoder({"甲": [1.0, 0.0]}, default=[0.0, 1.0], dim=2)
    assert encoder.encode(["甲"]) == [[1.0, 0.0]]
    assert encoder.encode(["乙"]) == [[0.0, 1.0]]
    assert encoder.calls == [["甲"], ["乙"]]


def test_bge_weights_ready_requires_config_and_weights(tmp_path) -> None:
    assert LocalBGEEmbedder.weights_ready(tmp_path / "missing") is False
    (tmp_path / "half").mkdir()
    (tmp_path / "half" / "config.json").write_text("{}", encoding="utf-8")
    assert LocalBGEEmbedder.weights_ready(tmp_path / "half") is False
    (tmp_path / "half" / "model.safetensors").write_bytes(b"x")
    assert LocalBGEEmbedder.weights_ready(tmp_path / "half") is True


def test_explicit_hash_never_touches_bge(tmp_path) -> None:
    selection = select_encoder(make_settings(tmp_path, text_encoder="hash"))
    assert selection.encoder.name == "hash-bigram-v1"
    assert selection.is_fallback is False


def test_auto_falls_back_to_hash_with_reason(tmp_path) -> None:
    """没有本地权重时自动落兜底，并给出可见的原因（约束 C3）。"""
    settings = make_settings(
        tmp_path, text_encoder="auto", embedding_model_dir=str(tmp_path / "no-weights")
    )
    selection = select_encoder(settings)
    assert selection.encoder.name == "hash-bigram-v1"
    assert selection.is_fallback is True
    assert "BGE" in (selection.fallback_reason or "")


def test_explicit_bge_raises_when_weights_missing(tmp_path) -> None:
    settings = make_settings(
        tmp_path, text_encoder="bge", embedding_model_dir=str(tmp_path / "no-weights")
    )
    with pytest.raises(EncoderUnavailable):
        select_encoder(settings)


def test_explicit_api_requires_key_and_model(tmp_path) -> None:
    settings = make_settings(tmp_path, text_encoder="api", llm_api_key="", embedding_api_model="")
    with pytest.raises(EncoderUnavailable):
        select_encoder(settings)

    settings = make_settings(
        tmp_path, text_encoder="api", llm_api_key="sk-test", embedding_api_model=""
    )
    with pytest.raises(EncoderUnavailable):
        select_encoder(settings)


def test_auto_never_selects_api(tmp_path) -> None:
    """哪怕配了 Key，auto 也不会自动转成联网计费的路线（决策 ② 的护栏）。"""
    settings = make_settings(
        tmp_path,
        text_encoder="auto",
        embedding_model_dir=str(tmp_path / "no-weights"),
        llm_api_key="sk-test",
        embedding_api_model="text-embedding-3-small",
    )
    assert select_encoder(settings).encoder.name == "hash-bigram-v1"


class _FakeEmbeddingsAPI:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors
        self.calls: list[dict] = []

    def create(self, *, model: str, input: list[str]):  # noqa: A002 - 对齐 SDK 参数名
        self.calls.append({"model": model, "input": list(input)})

        class _Item:
            def __init__(self, embedding):
                self.embedding = embedding

        class _Response:
            def __init__(self, data):
                self.data = data

        return _Response([_Item(vector) for vector in self.vectors[: len(input)]])


class _FakeOpenAI:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.embeddings = _FakeEmbeddingsAPI(vectors)


def test_api_embedder_parses_response_without_network() -> None:
    embedder = ApiEmbedder(
        base_url="https://api.deepseek.com/v1", api_key="sk-test", model="bge-m3"
    )
    fake = _FakeOpenAI([[3.0, 4.0]])
    embedder._client = fake

    vectors = embedder.encode(["一句话"])
    assert vectors == [[0.6, 0.8]], "云端向量要归一化后再入库"
    assert embedder.dim == 2, "维度按返回值记录，不预设"
    assert fake.embeddings.calls[0]["model"] == "bge-m3"
    assert embedder.name == "api:bge-m3"


def test_api_embedder_normalizes_blank_text() -> None:
    """空串在部分供应商那里会被拒，统一换成空格再发。"""
    embedder = ApiEmbedder(base_url="http://x", api_key="sk", model="m")
    fake = _FakeOpenAI([[1.0, 0.0]])
    embedder._client = fake
    embedder.encode([""])
    assert fake.embeddings.calls[0]["input"] == [" "]
