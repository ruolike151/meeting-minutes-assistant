"""多轮上下文（《M3 计划》5.7）。

两件事：

* **压缩**：最近 `history_rounds` 轮保留原文，更早的合并成一段摘要
  （落在 `conversations.summary`），避免上下文随轮数线性增长；
* **范围继承**：上一轮锁定了某场会议，这一轮说"那这场会还有什么风险"时应该沿用，
  而不是退回整个会议库——「我说的是哪场会」只该说一次。

摘要是**尽力而为**的：模型不可用时就保持原样（多轮对话照常能跑），
不因为摘要失败让对话不可用。
"""

from __future__ import annotations

import sqlite3

from app.config import DEFAULT_HISTORY_ROUNDS
from app.llm.cache import UsageTracker
from app.llm.client import ChatMessage, LLMClient, LLMUnavailable
from app.store.conversations_repo import ConversationRepo

# 超过这么多条消息（= 若干轮）才考虑压缩
COMPRESS_THRESHOLD = 8

SUMMARY_PROMPT = (
    "把下面这段会议助手对话压缩成要点，保留：用户问过哪些会议、锁定过哪场会、"
    "已经给过哪些结论。只输出压缩后的文字，不要解释。\n\n"
)


def load_history(
    conn: sqlite3.Connection,
    conversation_id: str | None,
    *,
    recent_rounds: int = DEFAULT_HISTORY_ROUNDS,
) -> tuple[list[ChatMessage], str]:
    """返回 (最近若干轮的原文消息, 更早轮次的摘要)。

    一轮 = 一问一答，所以保留的消息数是 `recent_rounds * 2`。
    """
    if not conversation_id:
        return [], ""
    repo = ConversationRepo(conn)
    conversation = repo.get(conversation_id)
    if conversation is None:
        return [], ""
    summary = repo.summary(conversation_id) or ""
    keep = max(1, int(recent_rounds)) * 2
    recent = conversation.messages[-keep:]
    return (
        [ChatMessage(role=message.role, content=message.content) for message in recent],
        summary,
    )


def scope_marker(meeting_ids: list[str]) -> str:
    """把范围写进助手消息里的可见标记（同时给人看，也给下一轮解析用）。"""
    if not meeting_ids:
        return ""
    return "[scope=%s]" % ",".join(meeting_ids)


def _extract_scope(text: str) -> list[str]:
    start = text.find("[scope=")
    if start < 0:
        return []
    end = text.find("]", start)
    if end < 0:
        return []
    raw = text[start + len("[scope=") : end]
    return [item.strip() for item in raw.split(",") if item.strip()]


def inherit_scope(history: list[ChatMessage], summary: str = "") -> list[str]:
    """从历史里找上一次锁定的会议范围（从最近往前找，命中即返回）。

    范围以 `[scope=...]` 标记出现在助手消息里，因此不用反解自然语言。
    """
    texts = [message.content or "" for message in reversed(history)]
    if summary:
        texts.append(summary)
    for content in texts:
        ids = _extract_scope(content)
        if ids:
            return ids
    return []


def compress_history(
    client: LLMClient | None,
    conn: sqlite3.Connection,
    conversation_id: str | None,
    *,
    model: str,
    usage: UsageTracker | None = None,
    threshold: int = COMPRESS_THRESHOLD,
) -> str | None:
    """消息数超过阈值时，用模型把较早的轮次压成摘要并落库。

    模型不可用或调用失败时返回 None，调用方保持原样。
    """
    if not conversation_id:
        return None
    repo = ConversationRepo(conn)
    conversation = repo.get(conversation_id)
    if conversation is None or len(conversation.messages) <= threshold:
        return None
    if client is None or not getattr(client, "available", False):
        return None

    older = conversation.messages[: -DEFAULT_HISTORY_ROUNDS * 2]
    if not older:
        return None
    payload = SUMMARY_PROMPT + "\n".join(
        "%s：%s" % (message.role, message.content) for message in older
    )
    try:
        result = client.complete([ChatMessage(role="user", content=payload)], model=model)
    except LLMUnavailable:
        return None
    if usage is not None:
        usage.record(result, model)
    summary = (result.text or "").strip()
    if not summary:
        return None
    repo.set_summary(conversation_id, summary)
    return summary
