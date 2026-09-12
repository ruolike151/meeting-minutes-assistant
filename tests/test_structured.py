"""结构化输出四级降级测试（《技术方案》10.2）。"""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict

from app.llm.client import ChatMessage
from app.llm.fake import FakeLLMClient
from app.llm.structured import (
    complete_structured,
    parse_json,
    response_format_for,
    schema_instruction,
    with_schema,
)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["query_content", "query_speaker"]
    reason: str = ""


GOOD = '{"intent": "query_content", "reason": "问的是会议内容"}'
BAD_SCHEMA = '{"intent": "unknown_intent"}'
NOT_JSON = "我觉得这个问题是在问会议内容。"


def test_parse_json_tolerates_code_fence_and_noise() -> None:
    wrapped = "好的，结果如下：\n```json\n%s\n```\n以上。" % GOOD
    assert parse_json(wrapped, Decision).intent == "query_content"


def test_parse_json_raises_on_invalid_payload() -> None:
    with pytest.raises(Exception):
        parse_json(NOT_JSON, Decision)


def test_response_format_depends_on_client_mode() -> None:
    assert response_format_for(FakeLLMClient(structured_mode="prompt"), Decision) is None
    assert response_format_for(FakeLLMClient(structured_mode="json_object"), Decision) == {
        "type": "json_object"
    }
    schema_format = response_format_for(FakeLLMClient(structured_mode="json_schema"), Decision)
    assert schema_format["type"] == "json_schema"
    assert schema_format["json_schema"]["name"] == "Decision"


def test_with_schema_appends_instruction() -> None:
    messages = with_schema([ChatMessage(role="user", content="问题")], Decision)
    assert len(messages) == 2
    assert messages[-1].role == "system"
    assert "JSON Schema" in messages[-1].content
    assert "intent" in schema_instruction(Decision)


def test_succeeds_on_first_attempt() -> None:
    client = FakeLLMClient([GOOD])
    outcome = complete_structured(
        client, [ChatMessage(role="user", content="问")], Decision, model="m"
    )
    assert outcome.ok is True
    assert outcome.attempts == 1
    assert outcome.value.intent == "query_content"
    assert client.call_count == 1


def test_retries_with_validation_error_feedback() -> None:
    client = FakeLLMClient([BAD_SCHEMA, GOOD])
    outcome = complete_structured(
        client, [ChatMessage(role="user", content="问")], Decision, model="m"
    )
    assert outcome.ok is True
    assert outcome.attempts == 2
    assert outcome.errors and "ValidationError" in outcome.errors[0]
    second_prompt = client.calls[1]["messages"][-1]["content"]
    assert "上一次的输出不合法" in second_prompt, "重试必须把错误原文回填给模型"


def test_gives_up_after_max_retries_and_returns_none() -> None:
    client = FakeLLMClient([NOT_JSON])
    outcome = complete_structured(
        client, [ChatMessage(role="user", content="问")], Decision, model="m", max_retries=2
    )
    assert outcome.ok is False
    assert outcome.value is None
    assert outcome.attempts == 3, "max_retries=2 表示总共 1 次初始 + 2 次重试"
    assert len(outcome.errors) == 3


def test_unavailable_client_returns_none_without_calling() -> None:
    client = FakeLLMClient([GOOD], available=False)
    outcome = complete_structured(
        client, [ChatMessage(role="user", content="问")], Decision, model="m"
    )
    assert outcome.ok is False
    assert client.call_count == 0
    assert "模型不可用" in outcome.errors[0]


def test_llm_unavailable_is_not_retried() -> None:
    """网络/Key 类失败直接抛出交给调用方降级，不把超时时间乘三。"""

    class Exploding:
        available = True
        structured_mode = "prompt"

        def complete(self, *args, **kwargs):
            from app.llm.client import LLMUnavailable

            raise LLMUnavailable("网络不通")

    with pytest.raises(Exception) as error:
        complete_structured(
            Exploding(), [ChatMessage(role="user", content="问")], Decision, model="m"
        )
    assert "网络不通" in str(error.value)
