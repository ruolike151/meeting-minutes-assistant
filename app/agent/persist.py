"""会话落库与产出模型（M1 在 pipeline.py 里，M3 抽出来避免循环依赖）。

`pipeline.py` 要驱动图，图要 import 节点，节点又要落库——把落库放在这里，
依赖方向就永远是单向的：`graph → nodes → persist → store`。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from app.agent.answers import Draft
from app.agent.history import scope_marker
from app.agent.verify import flatten_citations
from app.domain.models import Answer, Message
from app.store.conversations_repo import ConversationRepo
from app.store.repos import new_id, utcnow


@dataclass
class ChatResult:
    conversation_id: str
    user_message: Message
    assistant_message: Message
    answer: Answer
    trace: list = field(default_factory=list)


class PipelineError(RuntimeError):
    """编排层内部错误。接口层统一转 500，并保留原始信息便于排查。"""


def plain_text(answer: Answer) -> str:
    """把结构化回答压成纯文本，供会话导出与多轮上下文使用。

    末尾追加 `[scope=...]` 标记：下一轮问"那这场会还有什么"时，
    `history.inherit_scope` 靠它沿用范围，不用去反解自然语言。
    """
    parts = [answer.lead]
    for block in answer.blocks:
        if block.kind == "note" and block.text:
            parts.append(block.text)
            continue
        if not block.items:
            continue
        parts.append("【%s】" % (block.title or ""))
        for item in block.items:
            parts.append("- " + "：".join(part for part in (item.title, item.text) if part))
    if answer.footnote:
        parts.append(answer.footnote)
    marker = scope_marker(answer.scope_meeting_ids)
    if marker:
        parts.append(marker)
    return "\n".join(parts)


def persist_answer(
    conn: sqlite3.Connection, user_input: str, answer: Answer, conversation_id: str | None
) -> ChatResult:
    repo = ConversationRepo(conn)
    conversation = repo.ensure(conversation_id)
    now = utcnow()
    evidence = [citation.key for citation in answer.citations]
    user_message = Message(id=new_id("msg"), role="user", content=user_input, created_at=now)
    assistant_message = Message(
        id=new_id("msg"),
        role="assistant",
        content=plain_text(answer),
        evidence=evidence,
        answer=answer,
        created_at=now,
    )
    repo.append(conversation.id, user_message)
    repo.append(conversation.id, assistant_message)
    return ChatResult(
        conversation_id=conversation.id,
        user_message=user_message,
        assistant_message=assistant_message,
        answer=answer,
    )


def reload_assistant_message(result: ChatResult, conn: sqlite3.Connection) -> ChatResult:
    """把补写了 trace 之后的回答再落一次库（M1 的"补写一次"做法）。"""
    ConversationRepo(conn).append(result.conversation_id, result.assistant_message)
    return result


__all__ = [
    "ChatResult",
    "PipelineError",
    "Draft",
    "flatten_citations",
    "persist_answer",
    "plain_text",
    "reload_assistant_message",
]
