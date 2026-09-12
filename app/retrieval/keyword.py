"""关键词召回：SQLite FTS5 + BM25（《技术方案》9.1 第 2 步）。

只做一件事：给定查询与过滤条件，返回按 BM25 排序的片段候选。
是否采纳候选由 `pipeline.py` 的覆盖率与阈值决定，这里不做拒答判断。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from app.config import RECALL_K1
from app.retrieval.tokenize import content_tokens, match_expression


@dataclass(frozen=True)
class KeywordHit:
    meeting_id: str
    segment_id: str
    score: float      # BM25 取负，越大越相关

    @property
    def key(self) -> str:
        """融合用的稳定键：`meeting_id:segment_id`（与向量路的键同一格式）。"""
        return "%s:%s" % (self.meeting_id, self.segment_id)


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    meeting_ids: list[str] | None = None,
    speaker_ids: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = RECALL_K1,
    tokens: list[str] | None = None,
) -> list[KeywordHit]:
    """按关键词召回片段。

    `meeting_ids` / `speaker_ids` 是「先过滤再检索」的落地：
    把「这场会」变成确定性的 SQL 条件，而不是交给语义检索去碰运气。
    `date_from` / `date_to` 同理，过滤的是**会议日期**（`meetings.date`），
    因为片段自身只有相对会议开头的偏移秒数，没有绝对时间。
    """
    query_tokens = tokens if tokens is not None else content_tokens(query)
    if not query_tokens:
        return []

    sql = (
        "SELECT s.meeting_id AS meeting_id, s.id AS segment_id,"
        " bm25(segments_fts) AS score"
        " FROM segments_fts JOIN segments s ON s.rowid = segments_fts.rowid"
        " JOIN meetings m ON m.id = s.meeting_id"
        " WHERE segments_fts MATCH ?"
    )
    params: list[object] = [match_expression(query_tokens)]
    if meeting_ids:
        sql += " AND s.meeting_id IN (%s)" % ",".join("?" * len(meeting_ids))
        params.extend(meeting_ids)
    if speaker_ids:
        sql += " AND s.speaker_id IN (%s)" % ",".join("?" * len(speaker_ids))
        params.extend(speaker_ids)
    if date_from is not None:
        sql += " AND m.date >= ?"
        params.append(date_from.isoformat())
    if date_to is not None:
        sql += " AND m.date <= ?"
        params.append(date_to.isoformat())
    # bm25() 越小越相关，取负后按降序就是「越相关越靠前」
    sql += " ORDER BY score ASC LIMIT ?"
    params.append(int(limit))

    rows = conn.execute(sql, params).fetchall()
    return [
        KeywordHit(
            meeting_id=row["meeting_id"],
            segment_id=row["segment_id"],
            score=-float(row["score"]),
        )
        for row in rows
    ]
