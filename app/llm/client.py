"""LLM 适配协议（《技术方案》10.1）。

模型访问只经过这一个协议，切换供应商只改 base_url + model，
上层代码不感知是哪家。M1 不调用真实模型：答复由规则与模板装配，
协议与假实现先就位，M3 的 route / synthesize 节点直接复用。
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


@runtime_checkable
class LLMClient(Protocol):
    """最小可用协议：一次补全调用。"""

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str,
        temperature: float = 0.0,
        response_model: type[BaseModel] | None = None,
    ) -> LLMResult:  # pragma: no cover - 仅协议声明
        ...
