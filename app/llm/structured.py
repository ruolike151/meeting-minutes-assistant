"""结构化输出：四级降级（《技术方案》10.2 的落地）。

    1. 供应商支持 `json_schema` → 直接用 schema 约束；
    2. 只支持 JSON 模式 → `json_object` + 提示词内嵌 schema；
    3. Pydantic 校验失败 → 把 ValidationError 原文回填，重试（默认 ≤2 次）；
    4. 仍然失败 → 返回 None 让调用方**条目级降级**，不让整场会议失败。

第 4 条是关键：一条待办解析失败，不该让一次会议处理整体不可用。
因此这里不抛解析异常，只返回 `StructuredOutcome`，把"怎么办"留给调用方。

每次尝试的原始输出与错误都留在 outcome 里——它会被写进 trace，
面试现场能直接看到"模型第一次错在哪、第二次怎么改对的"。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Sequence

from pydantic import BaseModel, ValidationError

from app.llm.client import ChatMessage, LLMClient

REPAIR_TEMPLATE = (
    "上一次的输出不合法，无法通过 schema 校验。错误如下：\n%s\n"
    "请只输出一个符合上述 schema 的 JSON 对象，不要输出解释、不要用 Markdown 代码块。"
)


@dataclass
class StructuredOutcome:
    value: BaseModel | None
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    raw: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.value is not None


def response_format_for(client: LLMClient, model_cls: type[BaseModel]) -> dict | None:
    """按客户端能力给出 `response_format`；`prompt` 模式下不传这个参数。"""
    mode = getattr(client, "structured_mode", "prompt")
    if mode == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {
                "name": model_cls.__name__,
                "schema": model_cls.model_json_schema(),
                "strict": False,
            },
        }
    if mode == "json_object":
        return {"type": "json_object"}
    return None


def schema_instruction(model_cls: type[BaseModel]) -> str:
    schema = json.dumps(model_cls.model_json_schema(), ensure_ascii=False)
    return "只输出 JSON，且必须满足下面的 JSON Schema：\n%s" % schema


def with_schema(messages: Sequence[ChatMessage], model_cls: type[BaseModel]) -> list[ChatMessage]:
    """把 schema 作为一条 system 消息追加在最后（离生成最近，模型最不易忽略）。"""
    return list(messages) + [ChatMessage(role="system", content=schema_instruction(model_cls))]


def parse_json(text: str, model_cls: type[BaseModel]) -> BaseModel:
    """解析模型输出：容忍 ```json 代码块与前后噪声。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw[3:]
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
        raw = raw.split("```")[0]
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    return model_cls.model_validate(json.loads(raw))


def complete_structured(
    client: LLMClient,
    messages: Sequence[ChatMessage],
    model_cls: type[BaseModel],
    *,
    model: str,
    temperature: float = 0.0,
    max_retries: int = 2,
) -> StructuredOutcome:
    """调用模型并解析成 `model_cls`，失败自动带错误重试。

    `LLMUnavailable`（没 Key / 网络失败 / 缓存未命中）直接抛出，不重试：
    这类问题重试只是把超时时间乘三，交给调用方降级更诚实。
    """
    outcome = StructuredOutcome(value=None)
    if not getattr(client, "available", True):
        outcome.errors.append("模型不可用（未配置 MA_LLM_API_KEY 或未启用真实模式）")
        return outcome

    response_format = response_format_for(client, model_cls)
    attempt_messages = with_schema(messages, model_cls)
    # max_retries 是"重试次数"：总尝试次数 = 1 次初始 + max_retries 次重试
    for attempt in range(1, max(0, max_retries) + 2):
        outcome.attempts = attempt
        result = client.complete(
            attempt_messages,
            model=model,
            temperature=temperature,
            response_model=model_cls,
            response_format=response_format,
        )
        outcome.raw.append(result.text)
        try:
            outcome.value = parse_json(result.text, model_cls)
            return outcome
        except (ValidationError, json.JSONDecodeError, ValueError, TypeError) as error:
            outcome.errors.append("%s: %s" % (type(error).__name__, error))
            attempt_messages = list(attempt_messages) + [
                ChatMessage(role="user", content=REPAIR_TEMPLATE % error)
            ]
    return outcome
