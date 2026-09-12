"""片段向量索引（M3 T2）：把片段文本编码进 `segment_embeddings`。

三个入口，覆盖三条写入路径：

* `encode_segments`   —— 纯函数，输入片段输出向量，可单独测试；
* `ensure_meeting_index` —— 一场会议（种子数据、新处理的音频、重新处理）；
* `ensure_library_index` —— 整个库回填（老库升级到 v3 后补向量）。

回填是**按缺补写**：`missing_ids()` 只返回还没写过该编码器向量的片段，
所以重复调用是幂等的（第二次写入 0 行），启动时调用不会有额外开销。

编码器失败不抛给调用方：`ensure_*` 返回 (写入数, 说明)，
调用方决定是记一笔告警还是继续——这跟感知层的"降级而非崩溃"是同一条原则。
"""

from __future__ import annotations

import sqlite3
from typing import Sequence

from app.domain.models import Segment
from app.config import Settings
from app.retrieval.encoder import TextEncoder
from app.store.embeddings_repo import EmbeddingRepo
from app.store.repos import MeetingRepo, SegmentRepo


def encode_segments(
    encoder: TextEncoder, segments: Sequence[Segment], *, batch_size: int = 16
) -> dict[str, list[float]]:
    """把片段正文编码成向量，键是片段 ID。空片段照常参与（编码器自己处理空串）。"""
    if not segments:
        return {}
    vectors = encoder.encode([segment.text for segment in segments], batch_size=batch_size)
    return {segment.id: vector for segment, vector in zip(segments, vectors)}


def ensure_meeting_index(
    conn: sqlite3.Connection, encoder: TextEncoder, meeting_id: str
) -> tuple[int, str]:
    """给一场会议补齐缺失向量，并清掉悬空向量。返回 (新写入条数, 说明)。"""
    repo = EmbeddingRepo(conn)
    segments = SegmentRepo(conn).list_for_meeting(meeting_id)
    if not segments:
        return 0, ""
    try:
        missing = set(repo.missing_ids(meeting_id=meeting_id, encoder=encoder.storage_key))
        written = 0
        if missing:
            pending = [segment for segment in segments if segment.id in missing]
            vectors = encode_segments(encoder, pending)
            written = repo.upsert_many(
                meeting_id=meeting_id, encoder=encoder.storage_key, vectors=vectors
            )
        repo.prune(
            meeting_id=meeting_id,
            encoder=encoder.storage_key,
            keep_ids=[segment.id for segment in segments],
        )
    except Exception as error:  # noqa: BLE001 - 索引失败不该让会议处理整体失败
        return 0, "向量索引失败（%s: %s）" % (type(error).__name__, error)
    return written, ""


def ensure_library_index(
    conn: sqlite3.Connection, encoder: TextEncoder
) -> tuple[int, str]:
    """给库里所有会议补齐向量（启动时的老库回填）。"""
    total = 0
    notes: list[str] = []
    for meeting in MeetingRepo(conn).list():
        written, note = ensure_meeting_index(conn, encoder, meeting.id)
        total += written
        if note:
            notes.append("%s: %s" % (meeting.id, note))
    return total, "；".join(notes)


def index_meeting(settings: Settings, conn: sqlite3.Connection, meeting_id: str) -> str:
    """按配置给一场会议建向量索引，返回说明（空串表示没问题）。

    音频任务与编排层的生成节点共用这一个入口：索引只是增强，
    编码器缺失或失败都不该让会议处理失败（与感知层的"降级而非崩溃"同源）。
    """
    from app.retrieval.encoder import EncoderUnavailable, select_encoder

    try:
        encoder = select_encoder(settings).encoder
    except EncoderUnavailable as error:
        return "编码器不可用，已退回关键词检索：%s" % error
    _written, note = ensure_meeting_index(conn, encoder, meeting_id)
    return note
