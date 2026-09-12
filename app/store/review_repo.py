"""人工确认的审计仓储（M4）。

人工**结论**存在 `action_item_overrides`（M1 起就在，读取时合并进纪要）；
这里存的是人工**动作**：谁、什么时候、对哪一条、做了什么、改前改后是什么。

两者分开的理由与"纪要正文只读"同源：

* override 是当前状态，重算纪要后仍然生效（`MinutesRepo.get_with_overrides`）；
* event 是历史流水，用来回答"这条为什么被确认"、"人工一共介入了多少条"。

M5 的评测直接读这张表算人工介入率与驳回率，因此它不是日志，是数据。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from app.store.repos import new_id, utcnow

ReviewAction = str  # confirm | dismiss | edit | reopen


@dataclass(frozen=True)
class ReviewEvent:
    id: str
    meeting_id: str
    item_id: str
    kind: str
    action: str
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    actor: str = "user"
    created_at: str = ""


class ReviewEventRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record(
        self,
        *,
        meeting_id: str,
        item_id: str,
        kind: str,
        action: ReviewAction,
        before: dict | None = None,
        after: dict | None = None,
        actor: str = "user",
    ) -> ReviewEvent:
        """写一条审计。**不 commit**：它与 override 必须同事务生效。"""
        event = ReviewEvent(
            id=new_id("rev"),
            meeting_id=meeting_id,
            item_id=item_id,
            kind=kind,
            action=action,
            before=dict(before or {}),
            after=dict(after or {}),
            actor=actor,
            created_at=utcnow().isoformat(),
        )
        self.conn.execute(
            "INSERT INTO review_events(id, meeting_id, item_id, kind, action, before,"
            " after, actor, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                event.id,
                event.meeting_id,
                event.item_id,
                event.kind,
                event.action,
                json.dumps(event.before, ensure_ascii=False, default=str),
                json.dumps(event.after, ensure_ascii=False, default=str),
                event.actor,
                event.created_at,
            ),
        )
        return event

    def list_for_meeting(self, meeting_id: str) -> list[ReviewEvent]:
        rows = self.conn.execute(
            "SELECT * FROM review_events WHERE meeting_id = ? ORDER BY created_at, rowid",
            (meeting_id,),
        ).fetchall()
        return [_to_event(row) for row in rows]

    def list_for_item(self, meeting_id: str, item_id: str) -> list[ReviewEvent]:
        rows = self.conn.execute(
            "SELECT * FROM review_events WHERE meeting_id = ? AND item_id = ?"
            " ORDER BY created_at, rowid",
            (meeting_id, item_id),
        ).fetchall()
        return [_to_event(row) for row in rows]

    def count(self, *, action: str | None = None) -> int:
        if action is None:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM review_events").fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM review_events WHERE action = ?", (action,)
            ).fetchone()
        return int(row["n"]) if row else 0


def _to_event(row: sqlite3.Row) -> ReviewEvent:
    return ReviewEvent(
        id=row["id"],
        meeting_id=row["meeting_id"],
        item_id=row["item_id"],
        kind=row["kind"],
        action=row["action"],
        before=json.loads(row["before"] or "{}"),
        after=json.loads(row["after"] or "{}"),
        actor=row["actor"] or "user",
        created_at=row["created_at"],
    )


__all__ = ["ReviewAction", "ReviewEvent", "ReviewEventRepo"]
