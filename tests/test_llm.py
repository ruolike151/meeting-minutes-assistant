"""LLM 适配层测试：请求形状、缓存与记账、工厂模式选择（全程不联网）。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.config import Settings
from app.llm.cache import CachedLLMClient, UsageTracker, prompt_hash
from app.llm.client import ChatMessage, LLMResult, LLMUnavailable
from app.llm.fake import FakeLLMClient
from app.llm.factory import get_client, get_client_with_usage
from app.llm.openai_compat import OpenAICompatClient, estimate_cost, price_known


def make_settings(tmp_path, **changes) -> Settings:
    return replace(Settings.from_env(tmp_path / "data"), **changes)


class _FakeCompletions:
    def __init__(self, *, text: str = "{}", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.payloads: list[dict] = []

    def create(self, **payload):
        self.payloads.append(payload)
        if self.error is not None:
            raise self.error

        class _Message:
            content = self.text

        class _Choice:
            message = _Message()

        class _Usage:
            prompt_tokens = 120
            completion_tokens = 30

        class _Response:
            choices = [_Choice()]
            usage = _Usage()

        return _Response()


class _FakeOpenAI:
    def __init__(self, **kwargs) -> None:
        self.completions = _FakeCompletions(**kwargs)
        self.chat = type("Chat", (), {"completions": self.completions})()


def test_openai_client_builds_request_without_network() -> None:
    fake = _FakeOpenAI(text='{"ok": true}')
    client = OpenAICompatClient(
        base_url="https://api.deepseek.com/v1",
        api_key="sk-test",
        structured_mode="json_object",
        client=fake,
    )
    result = client.complete(
        [ChatMessage(role="user", content="你好")],
        model="deepseek-chat",
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    payload = fake.completions.payloads[0]
    assert payload["model"] == "deepseek-chat"
    assert payload["messages"] == [{"role": "user", "content": "你好"}]
    assert payload["temperature"] == 0.2
    assert payload["response_format"] == {"type": "json_object"}
    assert result.text == '{"ok": true}'
    assert (result.input_tokens, result.output_tokens) == (120, 30)
    assert result.cost > 0, "已知模型要按价目表估算费用"


def test_openai_client_omits_response_format_when_none() -> None:
    fake = _FakeOpenAI(text="{}")
    client = OpenAICompatClient(
        base_url="https://api.deepseek.com/v1", api_key="sk-test", client=fake
    )
    client.complete([ChatMessage(role="user", content="hi")], model="deepseek-chat")
    assert "response_format" not in fake.completions.payloads[0]


def test_openai_client_converts_errors_to_llm_unavailable() -> None:
    fake = _FakeOpenAI(error=RuntimeError("boom"))
    client = OpenAICompatClient(
        base_url="https://api.deepseek.com/v1", api_key="sk-test", client=fake
    )
    with pytest.raises(LLMUnavailable):
        client.complete([ChatMessage(role="user", content="hi")], model="deepseek-chat")


def test_openai_client_requires_key() -> None:
    with pytest.raises(LLMUnavailable):
        OpenAICompatClient(base_url="https://x", api_key="")


def test_estimate_cost_handles_unknown_model() -> None:
    assert estimate_cost("deepseek-chat", 1_000_000, 0) == 1.0
    assert estimate_cost("deepseek-chat", 0, 1_000_000) == 2.0
    assert estimate_cost("some-new-model", 1000, 1000) == 0.0
    assert estimate_cost("m", 1_000_000, 1_000_000, price_override=(3.0, 4.0)) == 7.0


def test_price_known_tells_caller_when_cost_will_be_zero() -> None:
    """未知模型不算价：冒烟脚本据此提示用户配价目表，而不是编一个数字。"""
    assert price_known("deepseek-chat") is True
    assert price_known("deepseek-flash") is False
    assert price_known("deepseek-flash", price_override=(1.0, 2.0)) is True


def test_prompt_hash_changes_with_every_input() -> None:
    messages = [ChatMessage(role="user", content="问一句")]
    base = prompt_hash(messages, model="m", temperature=0.0)
    assert base == prompt_hash(messages, model="m", temperature=0.0)
    assert base != prompt_hash(messages, model="other", temperature=0.0)
    assert base != prompt_hash(messages, model="m", temperature=0.5)
    assert base != prompt_hash(
        [ChatMessage(role="user", content="换个问题")], model="m", temperature=0.0
    )


def test_cached_client_stores_then_hits_without_calling_inner(conn) -> None:
    inner = FakeLLMClient("第一次的答案")
    client = CachedLLMClient(inner, conn)
    messages = [ChatMessage(role="user", content="这场会定了什么")]

    first = client.complete(messages, model="deepseek-chat")
    assert first.cached is False
    assert inner.call_count == 1
    assert client.cache_size() == 1

    second = client.complete(messages, model="deepseek-chat")
    assert second.cached is True
    assert second.text == first.text
    assert inner.call_count == 1, "命中缓存不该再打模型"
    assert second.cost == 0.0

    usage = client.usage.to_dict()
    assert usage["calls"] == 2
    assert usage["cache_hits"] == 1
    assert usage["input_tokens"] == first.input_tokens, "缓存命中不计 token"


def test_cached_client_offline_mode_reads_cache_but_never_calls(conn) -> None:
    messages = [ChatMessage(role="user", content="问题")]
    warm_inner = FakeLLMClient("答案")
    CachedLLMClient(warm_inner, conn).complete(messages, model="m")

    offline_inner = FakeLLMClient("不该被调用")
    offline = CachedLLMClient(offline_inner, conn, offline=True)
    hit = offline.complete(messages, model="m")
    assert hit.cached is True and hit.text == "答案"

    with pytest.raises(LLMUnavailable):
        offline.complete([ChatMessage(role="user", content="没预热过的问题")], model="m")
    assert offline_inner.call_count == 0


def test_usage_tracker_accumulates() -> None:
    tracker = UsageTracker()
    tracker.record(LLMResult(text="a", model="m", input_tokens=10, output_tokens=5, cost=0.1), "m")
    tracker.record(LLMResult(text="b", model="m", input_tokens=20, output_tokens=7, cost=0.2), "m")
    tracker.record(LLMResult(text="c", model="m", cost=0.3, cached=True), "m")
    data = tracker.to_dict()
    assert (data["calls"], data["cache_hits"]) == (3, 1)
    assert (data["input_tokens"], data["output_tokens"]) == (30, 12)
    assert data["cost"] == pytest.approx(0.3)


def test_factory_off_mode_returns_unavailable_fake(tmp_path, conn) -> None:
    settings = make_settings(tmp_path, llm_mode="off")
    client = get_client(settings, conn)
    assert client.available is False
    assert isinstance(client, FakeLLMClient)


def test_factory_auto_without_key_is_off(tmp_path, conn) -> None:
    settings = make_settings(tmp_path, llm_mode="auto", llm_api_key="")
    assert get_client(settings, conn).available is False


def test_factory_live_mode_wraps_cached_openai_client(tmp_path, conn) -> None:
    settings = make_settings(tmp_path, llm_mode="live", llm_api_key="sk-test")
    client, usage = get_client_with_usage(settings, conn)
    assert isinstance(client, CachedLLMClient)
    assert isinstance(client.inner, OpenAICompatClient)
    assert client.available is True
    assert client.structured_mode == "json_object"
    assert usage.to_dict()["calls"] == 0


def test_factory_cache_first_sets_offline(tmp_path, conn) -> None:
    settings = make_settings(tmp_path, llm_mode="cache_first", llm_api_key="sk-test")
    client = get_client(settings, conn)
    assert isinstance(client, CachedLLMClient)
    assert client.offline is True


def test_factory_rejects_non_openai_provider(tmp_path, conn) -> None:
    settings = make_settings(
        tmp_path, llm_mode="live", llm_api_key="sk-test", llm_provider="anthropic-native"
    )
    with pytest.raises(LLMUnavailable):
        get_client(settings, conn)


def test_fake_client_keyword_routing_records_response_format() -> None:
    client = FakeLLMClient(keywords={"路由": '{"intent": "query_content"}'})
    result = client.complete(
        [ChatMessage(role="user", content="请判断路由")], model="m", response_format={"type": "json_object"}
    )
    assert result.text == '{"intent": "query_content"}'
    assert client.calls[0]["response_format"] == {"type": "json_object"}
