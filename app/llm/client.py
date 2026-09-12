"""LLM 适配协议（《技术方案》10.1）。

模型访问只经过这一个协议，切换供应商只改 base_url + model，
上层代码不感知是哪家。

M3 起有三个实现：`OpenAICompatClient`（真实）、`CachedLLMClient`（缓存 + 记账）、
`FakeLLMClient`（测试与"无 Key 也能跑"的默认态）。三者的 `available` 属性让节点
可以**先问再做**——没有模型时走确定性模板，而不是发一个注定失败的请求。
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict


class ChatMessage(BaseModel):
    """对话消息（角色 + 内容），与领域层的业务 Message 不是一回事。"""

    model_config = ConfigDict(extra="forbid")

    role: str
    content: str


class LLMResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    cached: bool = False
    raw: dict[str, Any] | None = None


class LLMUnavailable(RuntimeError):
    """模型不可用：未配 Key、处于断网缓存模式且缓存未命中、或 SDK 调用失败。

    调用方据此降级到模板装配，而不是把异常抛给终端用户。
    """


@runtime_checkable
class LLMClient(Protocol):
    """最小可用协议：一次补全调用。"""

    available: bool
    structured_mode: str          # json_schema | json_object | prompt

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str,
        temperature: float = 0.0,
        response_model: type[BaseModel] | None = None,
        response_format: dict | None = None,
    ) -> LLMResult:  # pragma: no cover - 仅协议声明
        ...
