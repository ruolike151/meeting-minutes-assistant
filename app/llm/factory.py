"""LLM 客户端工厂（M3 T1）。

    MA_LLM_MODE=off          → FakeLLMClient(available=False)：全链路走确定性模板
    MA_LLM_MODE=cache_first  → 真实客户端 + 只读缓存（断网演示）
    MA_LLM_MODE=live         → 真实客户端 + 读写缓存 + 记账

`auto` 且没有 Key 时等价于 `off`——这是 C3 与"新克隆机器不误发请求"的落点。
真实调用前的最后一道校验是 provider：只认 OpenAI 兼容协议，
要接别的协议必须在这里加分支并补一组契约测试（不允许悄悄改行为）。
"""

from __future__ import annotations

import sqlite3

from app.config import VALID_LLM_PROVIDERS, Settings
from app.llm.cache import CachedLLMClient, UsageTracker
from app.llm.client import LLMClient, LLMUnavailable
from app.llm.fake import FakeLLMClient
from app.llm.openai_compat import OpenAICompatClient


def get_client(settings: Settings, conn: sqlite3.Connection) -> LLMClient:
    mode = settings.effective_llm_mode
    if mode == "off":
        return FakeLLMClient("", available=False)

    provider = (settings.llm_provider or "openai").strip().lower()
    if provider not in VALID_LLM_PROVIDERS:
        raise LLMUnavailable(
            "MA_LLM_PROVIDER 取值非法：%s（当前只实现 %s 兼容协议）"
            % (provider, " / ".join(VALID_LLM_PROVIDERS))
        )

    inner = OpenAICompatClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout=settings.llm_timeout,
        structured_mode=settings.llm_structured_mode,
        price_override=settings.llm_price_override,
    )
    return CachedLLMClient(inner, conn, offline=(mode == "cache_first"))


def get_client_with_usage(
    settings: Settings, conn: sqlite3.Connection
) -> tuple[LLMClient, UsageTracker]:
    """给"一次回答"用的入口：客户端与记账器一起返回，回答结束时写 trace。"""
    client = get_client(settings, conn)
    usage = client.usage if isinstance(client, CachedLLMClient) else UsageTracker()
    return client, usage
