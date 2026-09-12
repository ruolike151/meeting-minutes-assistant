"""LLM 适配层：协议与测试用假实现（《技术方案》10）。"""

from app.llm.client import ChatMessage, LLMClient, LLMResult
from app.llm.client import LLMUnavailable
from app.llm.cache import CachedLLMClient, UsageTracker
from app.llm.fake import FakeLLMClient
from app.llm.factory import get_client, get_client_with_usage
from app.llm.openai_compat import OpenAICompatClient
from app.llm.structured import StructuredOutcome, complete_structured

__all__ = [
    "ChatMessage",
    "LLMClient",
    "LLMResult",
    "LLMUnavailable",
    "CachedLLMClient",
    "UsageTracker",
    "FakeLLMClient",
    "OpenAICompatClient",
    "StructuredOutcome",
    "complete_structured",
    "get_client",
    "get_client_with_usage",
]
