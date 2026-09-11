"""仓储层：会议与片段的读写。

约定：
* 片段入库时同步写 `text_tokens`（jieba 预分词）并维护 FTS5 索引；
  检索层只负责查询，不负责建索引。
* 分词函数来自 `app/retrieval/tokenize.py`。它是一个不依赖其他模块的叶子模块，
  被存储层与检索层共用，不构成反向依赖。
* 纪要正文与人工修改分表存（见 `minutes_repo.py`），会话记录见 `conversations_repo.py`。
  按聚合拆模块是为了让每个文件保持在可读长度内，职责边界与文档一致。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime, timezone
from typing import Iterable, Sequence

from app.domain.models import (
    Meeting,
    Minutes,
    Participant,
    Segment,
)
from app.retrieval.tokenize import tokenize_for_index


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def new_id(prefix: str) -> str:
    return "%s_%s" % (prefix, uuid.uuid4().hex[:12])


class MeetingRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- 写 ---------------------------------------------------------

    def create(
        self,
        meeting: Meeting,
        segments: Sequence[Segment] = (),
        minutes: Minutes | None = None,
    ) -> Meeting:
        """整场会议一次落库，避免片段与纪要各写一半。"""
        created_at = meeting.created_at or utcnow()
        self.conn.execute(
            "INSERT OR REPLACE INTO meetings"
            "(id, title, date, time, duration, status, audio_file, created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (
                meeting.id,
                meeting.title,
                meeting.date.isoformat(),
                meeting.time,
                meeting.duration,
                meeting.status,
                meeting.audio_file,
                iso(created_at),
            ),
        )
        self.conn.execute("DELETE FROM participants WHERE meeting_id = ?", (meeting.id,))
        speaking = speaking_seconds(segments)
        for participant in meeting.participants:
            self.conn.execute(
                "INSERT INTO participants(meeting_id, speaker_id, name, role, speaking_seconds)"
                " VALUES(?,?,?,?,?)",
                (
                    meeting.id,
                    participant.id,
                    participant.name,
                    participant.role,
                    speaking.get(participant.id, participant.speaking_seconds),
                ),
            )
        SegmentRepo(self.conn).replace_for_meeting(meeting.id, segments)
        if minutes is not None:
            from app.store.minutes_repo import MinutesRepo

            MinutesRepo(self.conn).save(minutes)
        self.conn.commit()
        return meeting.model_copy(update={"created_at": created_at})

    def delete(self, meeting_id: str) -> None:
        """主要给测试与本地重置用；片段与纪要由外键级联删除。"""
        rows = self.conn.execute(
            "SELECT rowid FROM segments WHERE meeting_id = ?", (meeting_id,)
        ).fetchall()
        for row in rows:
            self.conn.execute("DELETE FROM segments_fts WHERE rowid = ?", (row["rowid"],))
        self.conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        self.conn.commit()

    # -- 读 ---------------------------------------------------------

    def exists(self, meeting_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)
        ).fetchone() is not None

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0])

    def get(self, meeting_id: str) -> Meeting | None:
        row = self.conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        return self.hydrate(row) if row else None

    def list(
        self,
        *,
        q: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        participant: str | None = None,
    ) -> list[Meeting]:
        """会议库筛选：标题 / 参会人 / 日期区间（产品文档 5.1）。"""
        sql = "SELECT DISTINCT m.* FROM meetings m"
        where: list[str] = []
        params: list[object] = []
        if participant:
            sql += " JOIN participants p ON p.meeting_id = m.id"
            where.append("(p.name LIKE ? OR p.speaker_id = ?)")
            params.extend(["%" + participant + "%", participant])
        if q:
            where.append(
                "(m.title LIKE ? OR EXISTS (SELECT 1 FROM participants p2"
                " WHERE p2.meeting_id = m.id AND p2.name LIKE ?))"
            )
            params.extend(["%" + q + "%", "%" + q + "%"])
        if date_from:
            where.append("m.date >= ?")
            params.append(date_from)
        if date_to:
            where.append("m.date <= ?")
            params.append(date_to)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY m.date DESC, m.id"
        return [self.hydrate(row) for row in self.conn.execute(sql, params).fetchall()]

    def hydrate(self, row: sqlite3.Row) -> Meeting:
        participants = self.conn.execute(
            "SELECT speaker_id, name, role, speaking_seconds FROM participants"
            " WHERE meeting_id = ? ORDER BY rowid",
            (row["id"],),
        ).fetchall()
        return Meeting(
            id=row["id"],
            title=row["title"],
            date=date.fromisoformat(row["date"]),
            time=row["time"],
            duration=row["duration"],
            status=row["status"],
            audio_file=row["audio_file"],
            participants=[
                Participant(
                    id=p["speaker_id"],
                    name=p["name"],
                    role=p["role"],
                    speaking_seconds=p["speaking_seconds"],
                )
                for p in participants
            ],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class SegmentRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def replace_for_meeting(self, meeting_id: str, segments: Sequence[Segment]) -> None:
        """整体替换某场会议的片段，并同步 FTS 索引。

        删除顺序不能颠倒：`segments_fts` 是 external content 表，
        FTS5 要靠内容表里的原始文本才能定位并移除索引项，
        所以必须先删索引行、再删内容行。顺序反过来会留下悬空索引项
        （tests/test_store.py::test_fts_index_survives_reindex 守住这一点）。
        """
        for row in self.conn.execute(
            "SELECT rowid FROM segments WHERE meeting_id = ?", (meeting_id,)
        ).fetchall():
            self.conn.execute("DELETE FROM segments_fts WHERE rowid = ?", (row["rowid"],))
        self.conn.execute("DELETE FROM segments WHERE meeting_id = ?", (meeting_id,))
        for segment in segments:
            tokens = tokenize_for_index(segment.text)
            cursor = self.conn.execute(
                "INSERT INTO segments(meeting_id, id, start, end, speaker_id,"
                " speaker_confidence, text, text_tokens) VALUES(?,?,?,?,?,?,?,?)",
                (
                    meeting_id,
                    segment.id,
                    segment.start,
                    segment.end,
                    segment.speaker_id,
                    segment.speaker_confidence,
                    segment.text,
                    tokens,
                ),
            )
            self.conn.execute(
                "INSERT INTO segments_fts(rowid, text_tokens) VALUES(?,?)",
                (cursor.lastrowid, tokens),
            )

    def count(self, meeting_id: str | None = None) -> int:
        if meeting_id:
            return int(
                self.conn.execute(
                    "SELECT COUNT(*) FROM segments WHERE meeting_id = ?", (meeting_id,)
                ).fetchone()[0]
            )
        return int(self.conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0])

    def list_for_meeting(self, meeting_id: str, *, speaker_id: str | None = None) -> list[Segment]:
        sql = "SELECT * FROM segments WHERE meeting_id = ?"
        params: list[object] = [meeting_id]
        if speaker_id:
            sql += " AND speaker_id = ?"
            params.append(speaker_id)
        sql += " ORDER BY start"
        return [row_to_segment(r) for r in self.conn.execute(sql, params).fetchall()]

    def list_all(self) -> list[Segment]:
        rows = self.conn.execute("SELECT * FROM segments ORDER BY meeting_id, start").fetchall()
        return [row_to_segment(r) for r in rows]

    def get(self, meeting_id: str, segment_id: str) -> Segment | None:
        row = self.conn.execute(
            "SELECT * FROM segments WHERE meeting_id = ? AND id = ?", (meeting_id, segment_id)
        ).fetchone()
        return row_to_segment(row) if row else None

    def get_many(self, keys: Iterable[tuple[str, str]]) -> dict[tuple[str, str], Segment]:
        found: dict[tuple[str, str], Segment] = {}
        for meeting_id, segment_id in keys:
            segment = self.get(meeting_id, segment_id)
            if segment is not None:
                found[(meeting_id, segment_id)] = segment
        return found


def speaking_seconds(segments: Sequence[Segment]) -> dict[str, float]:
    """按发言人累计发言时长，写进 participants.speaking_seconds。"""
    totals: dict[str, float] = {}
    for segment in segments:
        if not segment.speaker_id:
            continue
        totals[segment.speaker_id] = totals.get(segment.speaker_id, 0.0) + segment.duration
    return totals


def row_to_segment(row: sqlite3.Row) -> Segment:
    return Segment(
        id=row["id"],
        meeting_id=row["meeting_id"],
        start=row["start"],
        end=row["end"],
        speaker_id=row["speaker_id"],
        speaker_confidence=row["speaker_confidence"],
        text=row["text"],
    )
