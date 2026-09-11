"""FakeLLMClient（《技术方案》10.1）。

按调用序号返回预设响应，并可断言「模型被调了几次、prompt 里有什么」。
编排层的测试全部靠它，因此 CI 里不会出现任何真实网络调用（约束 C3）。
"""

from __future__ import annotations

from typing import Sequence

from pydantic import BaseModel

from app.llm.client import ChatMessage, LLMResult


class FakeLLMClient:
    def __init__(
        self,
        responses: Sequence[str] | str = "",
        *,
        model: str = "fake-model",
        fail_times: int = 0,
    ) -> None:
        if isinstance(responses, str):
            responses = [responses]
        self.responses = list(responses)
        self.model = model
        self.fail_times = fail_times
        self.calls: list[dict] = []

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        response_model: type[BaseModel] | None = None,
    ) -> LLMResult:
        self.calls.append(
            {
                "messages": [m.model_dump() for m in messages],
                "model": model or self.model,
                "temperature": temperature,
                "response_model": response_model.__name__ if response_model else None,
            }
        )
        index = len(self.calls) - 1
        if index < self.fail_times:
            raise RuntimeError("FakeLLMClient 预设的第 %d 次调用失败" % (index + 1))
        text = self.responses[index % len(self.responses)] if self.responses else ""
        return LLMResult(
            text=text,
            model=model or self.model,
            input_tokens=sum(len(m.content) for m in messages),
            output_tokens=len(text),
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)
