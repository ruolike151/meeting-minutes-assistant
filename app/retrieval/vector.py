"""向量召回（《技术方案》9.1 第 3 步）。

两件事：把过滤条件压进 SQL 取出候选向量（"先过滤再检索"），
再在候选集内做一次余弦排序。MVP 规模（几场会议、几百到几千片段）
一次矩阵乘就是毫秒级，不需要专用向量库——协议留在这里，
将来换 sqlite-vec / faiss 只改这个文件。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from app.config import RECALL_K2
from app.retrieval.encoder import TextEncoder, l2_normalize
from app.store.embeddings_repo import EmbeddingRepo


@dataclass(frozen=True)
class VectorHit:
    meeting_id: str
    segment_id: str
    score: float          # 余弦相似度，越大越相关

    @property
    def key(self) -> str:
        return "%s:%s" % (self.meeting_id, self.segment_id)


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("查询向量与候选向量维度不一致")
    return sum(float(a) * float(b) for a, b in zip(left, right))


def cosine_scores(
    query_vector: Sequence[float], candidates: Sequence[tuple[str, Sequence[float]]]
) -> list[tuple[str, float]]:
    """算余弦相似度，返回 `(key, score)`，按分降序。

    有 numpy 就用矩阵乘（几百上千条也是毫秒级），没有就退回纯 Python——
    这条兜底让检索层在最小依赖环境里也能跑（约束 C3）。
    """
    if not candidates:
        return []
    query = list(query_vector)
    pairs: list[tuple[str, float]]
    try:
        import numpy as np  # type: ignore

        matrix = np.asarray([list(vector) for _, vector in candidates], dtype="float32")
        query_array = np.asarray(query, dtype="float32")
        if matrix.size and matrix.shape[1] != query_array.shape[0]:
            raise ValueError("查询向量与候选向量维度不一致")
        scores = matrix @ query_array
        pairs = list(zip([key for key, _ in candidates], (float(value) for value in scores)))
    except ImportError:  # pragma: no cover - numpy 是主依赖，这里只是保险
        pairs = [(key, _dot(query, vector)) for key, vector in candidates]
    return sorted(pairs, key=lambda item: (-item[1], item[0]))


def vector_search(
    conn: sqlite3.Connection,
    encoder: TextEncoder,
    query: str,
    *,
    meeting_ids: Sequence[str] | None = None,
    speaker_ids: Sequence[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = RECALL_K2,
) -> list[VectorHit]:
    """按过滤条件取候选向量并排序，返回 top-`limit`。"""
    candidates = EmbeddingRepo(conn).candidates(
        encoder=encoder.storage_key,
        meeting_ids=meeting_ids,
        speaker_ids=speaker_ids,
        date_from=date_from,
        date_to=date_to,
    )
    if not candidates:
        return []
    payload = [(item.key, item.vector) for item in candidates]
    query_vector = l2_normalize(encoder.encode([query])[0])
    ranked = cosine_scores(query_vector, payload)[: max(0, int(limit))]
    return [
        VectorHit(
            meeting_id=key.split(":", 1)[0],
            segment_id=key.split(":", 1)[1],
            score=score,
        )
        for key, score in ranked
    ]
