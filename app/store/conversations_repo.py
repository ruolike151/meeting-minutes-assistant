"""会话仓储：会话与消息（产品文档 6 节 conversation / message）。

助手消息会连同完整的 `Answer` 载荷一起落库，这样重开页面能还原引用 chip，
不必重新跑一遍编排。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from app.domain.models import Answer, Conversation, Message
from app.store.repos import iso, new_id, utcnow


class ConversationRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(self, conversation_id: str | None = None) -> Conversation:
        conversation = Conversation(id=conversation_id or new_id("c"), created_at=utcnow())
        self.conn.execute(
            "INSERT INTO conversations(id, created_at) VALUES(?,?)",
            (conversation.id, iso(conversation.created_at)),
        )
        self.conn.commit()
        return conversation

    def ensure(self, conversation_id: str | None) -> Conversation:
        """有则复用，无则新建。前端刷新后继续同一个会话靠它。"""
        if conversation_id:
            existing = self.get(conversation_id)
            if existing is not None:
                return existing
            return self.create(conversation_id)
        return self.create()

    def append(self, conversation_id: str, message: Message) -> Message:
        payload = message.answer.model_dump(mode="json") if message.answer else None
        self.conn.execute(
            "INSERT OR REPLACE INTO messages"
            "(id, conversation_id, role, content, evidence, answer, created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                message.id,
                conversation_id,
                message.role,
                message.content,
                json.dumps(message.evidence, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False) if payload else None,
                iso(message.created_at),
            ),
        )
        self.conn.commit()
        return message

    def get(self, conversation_id: str) -> Conversation | None:
        row = self.conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if row is None:
            return None
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid",
            (conversation_id,),
        ).fetchall()
        return Conversation(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            messages=[row_to_message(r) for r in rows],
        )

    def list(self, limit: int = 50) -> list[Conversation]:
        rows = self.conn.execute(
            "SELECT id FROM conversations ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conversations = []
        for row in rows:
            conversation = self.get(row["id"])
            if conversation is not None:
                conversations.append(conversation)
        return conversations

    # -- 多轮上下文摘要（M3）---------------------------------------------

    def summary(self, conversation_id: str) -> str | None:
        """更早轮次的摘要；没压缩过就是 None。"""
        row = self.conn.execute(
            "SELECT summary FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if row is None:
            return None
        return row["summary"]

    def set_summary(self, conversation_id: str, summary: str) -> None:
        self.conn.execute(
            "UPDATE conversations SET summary = ? WHERE id = ?",
            (summary, conversation_id),
        )
        self.conn.commit()


def row_to_message(row: sqlite3.Row) -> Message:
    answer = Answer.model_validate(json.loads(row["answer"])) if row["answer"] else None
    return Message(
        id=row["id"],
        role=row["role"],
        content=row["content"],
        evidence=json.loads(row["evidence"] or "[]"),
        answer=answer,
        created_at=datetime.fromisoformat(row["created_at"]),
    )
