"""片段向量仓储（M3 T2）。

三个设计点：

1. **与 `segments` 分表存**（`segment_embeddings`）。`segments_fts` 是
   `content='segments'` 的外部内容表，对内容表做列变更会牵扯索引重建；
   向量另起一张表既不碰 FTS，也能让"同一个片段在不同编码器下的向量"并存。
2. **每条记录带 `encoder` 与 `dim`**：换编码器后旧向量不参与召回，
   沿用 M2 `speakers.encoder` 的做法——换模型后知道哪些向量失效。
3. **回填按缺补写**：`missing_ids()` 只返回缺向量的片段，重复调用写入 0 行。

时间过滤按**会议日期**（`meetings.date`）而不是片段偏移秒数：
「上周」指的是会议开在哪一周，片段自身只有相对会议开头的秒数。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from app.store.repos import iso
from app.store.vector_codec import pack_vector, unpack_vector


@dataclass(frozen=True)
class VectorCandidate:
    """候选向量：只有定位信息与向量本体，文本由调用方按需取。"""

    meeting_id: str
    segment_id: str
    vector: list[float]
    dim: int

    @property
    def key(self) -> str:
        return "%s:%s" % (self.meeting_id, self.segment_id)


class EmbeddingRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- 写 ---------------------------------------------------------

    def upsert_many(
        self,
        *,
        meeting_id: str,
        encoder: str,
        vectors: dict[str, Sequence[float]],
    ) -> int:
        """批量写入某场会议的片段向量，返回写入条数。"""
        if not vectors:
            return 0
        rows = []
        now = iso()
        for segment_id, vector in vectors.items():
            values = [float(value) for value in vector]
            rows.append(
                (meeting_id, segment_id, encoder, len(values), pack_vector(values), now)
            )
        self.conn.executemany(
            "INSERT OR REPLACE INTO segment_embeddings"
            "(meeting_id, segment_id, encoder, dim, vector, created_at)"
            " VALUES(?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def delete_for_meeting(self, meeting_id: str, *, encoder: str | None = None) -> None:
        if encoder:
            self.conn.execute(
                "DELETE FROM segment_embeddings WHERE meeting_id = ? AND encoder = ?",
                (meeting_id, encoder),
            )
        else:
            self.conn.execute(
                "DELETE FROM segment_embeddings WHERE meeting_id = ?", (meeting_id,)
            )
        self.conn.commit()

    def prune(self, *, meeting_id: str, encoder: str, keep_ids: Sequence[str]) -> int:
        """删掉已经不存在的片段留下的悬空向量，返回删除条数。

        会议被重新处理（片段 ID 变化）时，旧向量必须跟着走，
        否则检索会召回一批库里已经没有正文的片段。
        """
        if keep_ids:
            placeholders = ",".join("?" * len(keep_ids))
            sql = (
                "DELETE FROM segment_embeddings WHERE meeting_id = ? AND encoder = ?"
                " AND segment_id NOT IN (%s)" % placeholders
            )
            cursor = self.conn.execute(sql, (meeting_id, encoder, *keep_ids))
        else:
            cursor = self.conn.execute(
                "DELETE FROM segment_embeddings WHERE meeting_id = ? AND encoder = ?",
                (meeting_id, encoder),
            )
        self.conn.commit()
        return int(cursor.rowcount or 0)

    # -- 读 ---------------------------------------------------------

    def get(
        self, meeting_id: str, segment_id: str, *, encoder: str
    ) -> list[float] | None:
        row = self.conn.execute(
            "SELECT dim, vector FROM segment_embeddings"
            " WHERE meeting_id = ? AND segment_id = ? AND encoder = ?",
            (meeting_id, segment_id, encoder),
        ).fetchone()
        if row is None:
            return None
        return unpack_vector(row["vector"], int(row["dim"]))

    def count(self, *, encoder: str | None = None) -> int:
        if encoder:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM segment_embeddings WHERE encoder = ?", (encoder,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM segment_embeddings").fetchone()
        return int(row["n"]) if row else 0

    def missing_ids(self, *, meeting_id: str, encoder: str) -> list[str]:
        """还没写过该编码器向量的片段 ID（按时间排序，回填顺序稳定）。

        `encoder` 是带维度的存储键（见 `TextEncoder.storage_key`），
        所以"同一个名字、不同维度"的两套向量不会互相当成已建好。
        """
        rows = self.conn.execute(
            "SELECT s.id AS id FROM segments s"
            " LEFT JOIN segment_embeddings e ON e.meeting_id = s.meeting_id"
            "   AND e.segment_id = s.id AND e.encoder = ?"
            " WHERE s.meeting_id = ? AND e.segment_id IS NULL"
            " ORDER BY s.start",
            (encoder, meeting_id),
        ).fetchall()
        return [row["id"] for row in rows]

    def candidates(
        self,
        *,
        encoder: str,
        meeting_ids: Sequence[str] | None = None,
        speaker_ids: Sequence[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[VectorCandidate]:
        """按过滤条件下推，取出候选向量（"先过滤再检索"的落地）。"""
        sql = (
            "SELECT e.meeting_id AS meeting_id, e.segment_id AS segment_id,"
            " e.vector AS vector, e.dim AS dim"
            " FROM segment_embeddings e"
            " JOIN segments s ON s.meeting_id = e.meeting_id AND s.id = e.segment_id"
            " JOIN meetings m ON m.id = e.meeting_id"
            " WHERE e.encoder = ?"
        )
        params: list[object] = [encoder]
        if meeting_ids:
            sql += " AND e.meeting_id IN (%s)" % ",".join("?" * len(meeting_ids))
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
        sql += " ORDER BY e.meeting_id, s.start"

        rows = self.conn.execute(sql, params).fetchall()
        return [
            VectorCandidate(
                meeting_id=row["meeting_id"],
                segment_id=row["segment_id"],
                vector=unpack_vector(row["vector"], int(row["dim"])),
                dim=int(row["dim"]),
            )
            for row in rows
        ]
