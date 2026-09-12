"""声纹库仓储（M2 T5）。

两张表：

* `speakers`：一个人（id / 姓名 / 角色 / 编码器 / 维度 / 质心）；
* `speaker_embeddings`：这个人注册音频切窗后的多枚向量。

为什么存多枚向量：匹配用的是「每人 top-k 相似度均值」（见 `voiceref.py`），
只存质心就把算法降级成质心比对，对注册音频里的停顿、咳嗽、串音更敏感。

向量用 float32 小端字节流存 BLOB：与 numpy 无关，仓储层因此可以离线单测；
换编码器后旧向量不能混用，所以每条记录都带 `encoder` 与 `dim`。
"""

from __future__ import annotations

import sqlite3
import struct
import uuid
from dataclasses import dataclass
from typing import Sequence

from app.perception.voiceref import VoiceProfile, build_profile
from app.store.repos import iso, utcnow


def pack_vector(vector: Sequence[float]) -> bytes:
    values = [float(value) for value in vector]
    if not values:
        raise ValueError("声纹向量不能为空")
    return struct.pack("<%df" % len(values), *values)


def unpack_vector(blob: bytes, dim: int) -> list[float]:
    if dim <= 0:
        raise ValueError("向量维度必须为正数")
    expected = dim * 4
    if len(blob) != expected:
        raise ValueError("声纹向量长度不符：期望 %d 字节，实际 %d" % (expected, len(blob)))
    return list(struct.unpack("<%df" % dim, blob))


def new_speaker_id(name: str) -> str:
    return "spk_" + uuid.uuid4().hex[:8]


@dataclass(frozen=True)
class SpeakerRecord:
    id: str
    name: str
    role: str | None
    encoder: str
    dim: int
    vector_count: int = 0


def _to_record(row: sqlite3.Row, vector_count: int = 0) -> SpeakerRecord:
    return SpeakerRecord(
        id=row["id"],
        name=row["name"],
        role=row["role"],
        encoder=row["encoder"],
        dim=int(row["dim"]),
        vector_count=vector_count,
    )


class SpeakerRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- 写 ---------------------------------------------------------

    def upsert(
        self,
        *,
        name: str,
        vectors: Sequence[Sequence[float]],
        role: str | None = None,
        encoder: str = "speechbrain_ecapa",
        speaker_id: str | None = None,
    ) -> SpeakerRecord:
        """按姓名注册或更新声纹（同名覆盖，避免同一个人两张声纹）。"""
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("声纹姓名不能为空")

        existing = self.get_by_name(clean_name)
        profile = build_profile(existing.id if existing else (speaker_id or new_speaker_id(clean_name)), vectors)
        now = iso(utcnow())

        if existing:
            self.conn.execute(
                "UPDATE speakers SET role = ?, encoder = ?, dim = ?, centroid = ?,"
                " updated_at = ? WHERE id = ?",
                (role, encoder, profile.dim, pack_vector(profile.centroid), now, existing.id),
            )
            self.conn.execute(
                "DELETE FROM speaker_embeddings WHERE speaker_id = ?", (existing.id,)
            )
        else:
            self.conn.execute(
                "INSERT INTO speakers(id, name, role, encoder, dim, centroid, created_at,"
                " updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    profile.speaker_id,
                    clean_name,
                    role,
                    encoder,
                    profile.dim,
                    pack_vector(profile.centroid),
                    now,
                    now,
                ),
            )

        for index, vector in enumerate(profile.vectors):
            self.conn.execute(
                "INSERT INTO speaker_embeddings(speaker_id, idx, vector) VALUES(?,?,?)",
                (profile.speaker_id, index, pack_vector(vector)),
            )
        self.conn.commit()

        record = self.get(profile.speaker_id)
        assert record is not None
        return record

    def delete(self, speaker_id: str) -> None:
        self.conn.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))
        self.conn.commit()

    # -- 读 ---------------------------------------------------------

    def get(self, speaker_id: str) -> SpeakerRecord | None:
        row = self.conn.execute("SELECT * FROM speakers WHERE id = ?", (speaker_id,)).fetchone()
        if row is None:
            return None
        return _to_record(row, self._count(speaker_id))

    def get_by_name(self, name: str) -> SpeakerRecord | None:
        row = self.conn.execute("SELECT * FROM speakers WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        return _to_record(row, self._count(row["id"]))

    def all(self) -> list[SpeakerRecord]:
        rows = self.conn.execute("SELECT * FROM speakers ORDER BY created_at").fetchall()
        return [_to_record(row, self._count(row["id"])) for row in rows]

    def vectors(self, speaker_id: str) -> list[list[float]]:
        rows = self.conn.execute(
            "SELECT idx, vector FROM speaker_embeddings WHERE speaker_id = ? ORDER BY idx",
            (speaker_id,),
        ).fetchall()
        if not rows:
            return []
        row = self.conn.execute("SELECT dim FROM speakers WHERE id = ?", (speaker_id,)).fetchone()
        dim = int(row["dim"]) if row else 0
        return [unpack_vector(item["vector"], dim) for item in rows]

    def profiles(self, *, encoder: str | None = None) -> dict[str, VoiceProfile]:
        """加载声纹库。只取编码器一致的记录——换模型后旧向量不能混用。"""
        result: dict[str, VoiceProfile] = {}
        for record in self.all():
            if encoder and record.encoder != encoder:
                continue
            vectors = self.vectors(record.id)
            if not vectors:
                continue
            result[record.id] = build_profile(record.id, vectors)
        return result

    def _count(self, speaker_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM speaker_embeddings WHERE speaker_id = ?", (speaker_id,)
        ).fetchone()
        return int(row["n"]) if row else 0