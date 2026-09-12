"""OpenAI 兼容客户端（《技术方案》10.1）。

**全项目唯一 import `openai` 的地方**，也是唯一发真实网络请求的地方。
换厂商（DeepSeek / 通义 / GLM / OpenAI）只改 `.env` 里的 base_url + 模型名，
上层代码一行不动——《技术方案》4.2 那句"供应商切换只改一处配置"的落点。

三个细节：

* `structured_mode` 由配置决定（`json_schema` / `json_object` / `prompt`）：
  不是所有 OpenAI 兼容端点都支持 `json_schema`，DeepSeek 走 `json_object`。
  具体降级顺序见 `structured.py`；
* **token 与费用在客户端就地记录**：调用方不需要自己算（`LLMResult` 里带回）；
* `api_key` 只存在实例属性里，不进日志、不写库、不随 trace 落盘。
"""

from __future__ import annotations

from typing import Sequence

from pydantic import BaseModel

from app.llm.client import ChatMessage, LLMResult, LLMUnavailable

# 近似价目表（人民币 / 百万 token），只用于"这一轮大概花了多少"的相对比较，
# 不作为账单依据；换模型或调价时用 MA_LLM_PRICE_INPUT / MA_LLM_PRICE_OUTPUT 覆盖。
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "deepseek-chat": (1.0, 2.0),
    "deepseek-reasoner": (4.0, 16.0),
}


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    price_override: tuple[float, float] | None = None,
) -> float:
    """按 token 数估算费用；未知模型返回 0（宁可少算，也不编一个数出来）。"""
    in_price, out_price = price_override or MODEL_PRICES.get(model, (0.0, 0.0))
    return round(
        input_tokens / 1_000_000 * in_price + output_tokens / 1_000_000 * out_price, 6
    )


def price_known(model: str, *, price_override: tuple[float, float] | None = None) -> bool:
    """价目表里有没有这个模型。

    没有的话费用会记 0——这是有意的选择：宁可显示"未知"，
    也不要拿另一个模型的单价硬套出一个看起来精确的错数字。
    调用方（冒烟脚本）据此提示用户配置 `MA_LLM_PRICE_INPUT/OUTPUT`。
    """
    return price_override is not None or model in MODEL_PRICES


class OpenAICompatClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 60.0,
        structured_mode: str = "json_object",
        price_override: tuple[float, float] | None = None,
        client: object | None = None,
    ) -> None:
        if not api_key:
            raise LLMUnavailable("未配置 MA_LLM_API_KEY")
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.structured_mode = structured_mode
        self.price_override = price_override
        self.available = True
        self._client = client

    @property
    def name(self) -> str:
        return "openai:%s" % self.base_url

    def _load(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI  # type: ignore
        except Exception as error:  # pragma: no cover
            raise LLMUnavailable("未安装 openai SDK：%s" % error) from error
        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout)
        return self._client

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str,
        temperature: float = 0.0,
        response_model: type[BaseModel] | None = None,
        response_format: dict | None = None,
    ) -> LLMResult:
        client = self._load()
        payload: dict = {"model": model, "messages": [m.model_dump() for m in messages]}
        if temperature is not None:
            payload["temperature"] = temperature
        if response_format is not None:
            payload["response_format"] = response_format
        try:
            response = client.chat.completions.create(**payload)
        except Exception as error:  # noqa: BLE001 - 统一转成可降级的异常类型
            raise LLMUnavailable("模型调用失败：%s: %s" % (type(error).__name__, error)) from error

        text = _first_text(response)
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return LLMResult(
            text=text,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=estimate_cost(
                model, input_tokens, output_tokens, price_override=self.price_override
            ),
        )


def _first_text(response: object) -> str:
    """取第一条候选的文本。部分兼容端点返回 dict，这里两种形状都认。"""
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        return ""
    first = choices[0]
    message = getattr(first, "message", None)
    if message is None and isinstance(first, dict):
        message = first.get("message")
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return content or ""
