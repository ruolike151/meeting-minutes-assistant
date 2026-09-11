"""纪要仓储与人工修改合并。

两个关键点（《技术方案》6.2）：
1. 纪要正文整份 JSON 存 `minutes.payload`，Pydantic 负责校验；
2. 人工修改单独存 `action_item_overrides`，读取时合并，
   因此重新生成纪要不会把人工确认的结果覆盖掉。

override 的 `resolution` 有三种状态，对应人在回路的三个动作：
confirmed（确认并入最终稿）、dismissed（丢弃）、pending（重新挂起待确认）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from app.domain.models import ActionItem, Meeting, Minutes
from app.store.repos import MeetingRepo, iso, parse_date, utcnow


class MinutesRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, minutes: Minutes) -> Minutes:
        updated_at = minutes.updated_at or utcnow()
        self.conn.execute(
            "INSERT OR REPLACE INTO minutes(meeting_id, payload, schema_version, updated_at)"
            " VALUES(?,?,?,?)",
            (
                minutes.meeting_id,
                json.dumps(minutes.model_dump(mode="json"), ensure_ascii=False),
                minutes.schema_version,
                iso(updated_at),
            ),
        )
        self.conn.commit()
        return minutes.model_copy(update={"updated_at": updated_at})

    def get(self, meeting_id: str) -> Minutes | None:
        row = self.conn.execute(
            "SELECT payload, updated_at FROM minutes WHERE meeting_id = ?", (meeting_id,)
        ).fetchone()
        if row is None:
            return None
        minutes = Minutes.model_validate(json.loads(row["payload"]))
        return minutes.model_copy(update={"updated_at": datetime.fromisoformat(row["updated_at"])})

    def get_with_overrides(self, meeting_id: str) -> Minutes | None:
        """读取纪要并合并人工修改：override 优先于模型生成结果。"""
        minutes = self.get(meeting_id)
        if minutes is None:
            return None
        return apply_overrides(minutes, OverrideRepo(self.conn).list_for_meeting(meeting_id))

    def action_items(self, meeting_id: str) -> list[ActionItem]:
        minutes = self.get_with_overrides(meeting_id)
        return list(minutes.action_items) if minutes else []

    def list_all_action_items(self) -> list[tuple[Meeting, ActionItem]]:
        rows = self.conn.execute("SELECT meeting_id FROM minutes ORDER BY meeting_id").fetchall()
        result: list[tuple[Meeting, ActionItem]] = []
        for row in rows:
            meeting = MeetingRepo(self.conn).get(row["meeting_id"])
            if meeting is None:
                continue
            for item in self.action_items(row["meeting_id"]):
                result.append((meeting, item))
        return result


class OverrideRepo:
    """人工修改：确认 / 改负责人 / 改截止时间 / 标记完成 / 丢弃。"""

    FIELDS = ("status", "owner", "due", "resolution", "note")

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(self, meeting_id: str, item_id: str, **changes: str | None) -> dict:
        current = self.get(meeting_id, item_id) or {"meeting_id": meeting_id, "item_id": item_id}
        for field in self.FIELDS:
            if field in changes:
                current[field] = changes[field]
        self.conn.execute(
            "INSERT INTO action_item_overrides"
            "(meeting_id, item_id, status, owner, due, resolution, note, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)"
            " ON CONFLICT(meeting_id, item_id) DO UPDATE SET"
            " status=excluded.status, owner=excluded.owner, due=excluded.due,"
            " resolution=excluded.resolution, note=excluded.note,"
            " updated_at=excluded.updated_at",
            (
                meeting_id,
                item_id,
                current.get("status"),
                current.get("owner"),
                current.get("due"),
                current.get("resolution"),
                current.get("note"),
                iso(),
            ),
        )
        self.conn.commit()
        return current

    def get(self, meeting_id: str, item_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM action_item_overrides WHERE meeting_id = ? AND item_id = ?",
            (meeting_id, item_id),
        ).fetchone()
        return dict(row) if row else None

    def list_for_meeting(self, meeting_id: str) -> dict[str, dict]:
        rows = self.conn.execute(
            "SELECT * FROM action_item_overrides WHERE meeting_id = ?", (meeting_id,)
        ).fetchall()
        return {row["item_id"]: dict(row) for row in rows}

    def list_all(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM action_item_overrides").fetchall()
        return [dict(row) for row in rows]


def apply_overrides(minutes: Minutes, overrides: dict[str, dict]) -> Minutes:
    """把人工修改合并进纪要。

    * resolution=confirmed：条目并入最终稿，退出待确认区；
    * resolution=dismissed：条目被丢弃；
    * resolution=pending：条目重新挂起，等人工处理（flag_uncertainty 用）；
    * status / owner / due 直接覆盖生成结果。

    确认不等于改写置信度：模型原本的 confidence 原样保留，
    只是把把关的人从置信度阈值换成了人工。
    """
    if not overrides:
        return minutes
    items: list[ActionItem] = []
    for item in minutes.action_items:
        override = overrides.get(item.id)
        if override is None:
            items.append(item)
            continue
        resolution = override.get("resolution")
        if resolution == "dismissed":
            continue
        changes: dict = {}
        if override.get("status"):
            changes["status"] = override["status"]
        if override.get("owner"):
            changes["owner"] = override["owner"]
            if item.owner_source in ("missing", "unknown", "inferred"):
                # 人工补录负责人后来源改为 confirmed，便于区分模型判断与人工输入
                changes["owner_source"] = "confirmed"
        if override.get("due"):
            changes["due"] = parse_date(override["due"])
        if resolution == "confirmed":
            changes["needs_review"] = False
            changes["confirmed"] = True
            changes["review_reason"] = None
        elif resolution == "pending":
            changes["needs_review"] = True
            changes["review_reason"] = (
                override.get("note") or item.review_reason or "人工标记为待确认"
            )
        items.append(item.model_copy(update=changes))
    return minutes.model_copy(update={"action_items": items})


def review_items(minutes: Minutes) -> list[ActionItem]:
    """待确认区条目：needs_review 为真且尚未被确认 / 丢弃。"""
    return [item for item in minutes.action_items if item.needs_review]