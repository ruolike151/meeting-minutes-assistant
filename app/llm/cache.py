"""LLM 缓存与记账（《技术方案》10.3）。

一个装饰器解决三件事：

* **缓存**：`prompt_hash` 命中就返回旧响应，token 与费用都记 0，
  `cached=True` 让上层知道"这次没花钱"；
* **记账**：把每次调用的 token / 费用累加，回答结束时一次性写进 `chat_traces`；
* **断网演示**：`offline=True` 时只读缓存，未命中就抛 `LLMUnavailable`，
  让链路降级到模板而不是卡在一次超时上（面试现场的兜底开关）。

缓存键包含 model / temperature / 全部消息 / 期望 schema，
任何一项变了都算不同的请求——宁可少命中，也不要把上一个问题的答案喂给下一个问题。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Sequence

from pydantic import BaseModel

from app.llm.client import ChatMessage, LLMClient, LLMResult, LLMUnavailable
from app.store.repos import iso


def prompt_hash(
    messages: Sequence[ChatMessage],
    *,
    model: str,
    temperature: float,
    response_model: type[BaseModel] | None = None,
    response_format: dict | None = None,
) -> str:
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [m.model_dump() for m in messages],
        "schema": response_model.__name__ if response_model else None,
        "response_format": response_format,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class UsageTracker:
    """token / 费用累加器（一次回答 = 一个实例）。"""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = 0.0
        self.calls = 0
        self.cache_hits = 0
        self.models: set[str] = set()

    def record(self, result: LLMResult, model: str) -> None:
        self.calls += 1
        if result.cached:
            self.cache_hits += 1
            return
        self.input_tokens += int(result.input_tokens)
        self.output_tokens += int(result.output_tokens)
        self.cost = round(self.cost + float(result.cost), 6)
        self.models.add(model)

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost": self.cost,
            "models": sorted(self.models),
        }


class CachedLLMClient:
    def __init__(
        self,
        inner: LLMClient,
        conn: sqlite3.Connection,
        *,
        offline: bool = False,
        usage: UsageTracker | None = None,
    ) -> None:
        self.inner = inner
        self.conn = conn
        self.offline = offline
        self.usage = usage or UsageTracker()
        self.available = getattr(inner, "available", True)
        self.structured_mode = getattr(inner, "structured_mode", "prompt")

    @property
    def name(self) -> str:
        return "cached(%s)" % getattr(self.inner, "name", type(self.inner).__name__)

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str,
        temperature: float = 0.0,
        response_model: type[BaseModel] | None = None,
        response_format: dict | None = None,
    ) -> LLMResult:
        key = prompt_hash(
            messages,
            model=model,
            temperature=temperature,
            response_model=response_model,
            response_format=response_format,
        )
        row = self.conn.execute(
            "SELECT response, input_tokens, output_tokens, cost FROM llm_cache"
            " WHERE prompt_hash = ?",
            (key,),
        ).fetchone()
        if row is not None:
            result = LLMResult(
                text=row["response"],
                model=model,
                input_tokens=int(row["input_tokens"]),
                output_tokens=int(row["output_tokens"]),
                cost=0.0,
                cached=True,
            )
            self.usage.record(result, model)
            return result

        if self.offline:
            raise LLMUnavailable("缓存未命中，且当前处于只读缓存模式（MA_LLM_MODE=cache_first）")

        result = self.inner.complete(
            messages,
            model=model,
            temperature=temperature,
            response_model=response_model,
            response_format=response_format,
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO llm_cache"
            "(prompt_hash, model, response, input_tokens, output_tokens, cost, created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                key,
                model,
                result.text,
                int(result.input_tokens),
                int(result.output_tokens),
                float(result.cost),
                iso(),
            ),
        )
        self.conn.commit()
        self.usage.record(result, model)
        return result

    def cache_size(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0])
