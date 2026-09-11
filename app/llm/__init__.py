"""LLM 适配层：协议与测试用假实现（《技术方案》10）。"""

from app.llm.client import ChatMessage, LLMClient, LLMResult
from app.llm.fake import FakeLLMClient

__all__ = ["ChatMessage", "LLMClient", "LLMResult", "FakeLLMClient"]
