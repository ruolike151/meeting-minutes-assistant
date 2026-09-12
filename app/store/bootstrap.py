"""建库与固定样例数据装载。

`bootstrap()` 是进程启动与测试共用的入口：建表 -> 首次运行时写入样例数据。
样例数据只写一次（按会议数量判断），重复启动不会累积重复记录。
"""

from __future__ import annotations

import sqlite3

from app.config import Settings
from app.domain.models import Meeting, Minutes, Participant, Segment
from app.retrieval.encoder import EncoderUnavailable, select_encoder
from app.retrieval.index import ensure_library_index
from app.retrieval.tokenize import register_words
from app.store.db import open_db
from app.store.repos import MeetingRepo
from app.store.seeds import MEETINGS, SPEAKERS


def bootstrap(settings: Settings, *, index_vectors: bool = True) -> sqlite3.Connection:
    """开库并保证样例数据就绪，返回可用的连接。

    M3 起多一步：给还没建向量的片段补上向量（幂等，第二次是空操作）。
    索引失败只记一笔告警、不影响启动——向量是查得更好的手段，
    不该成为"能不能启动"的前提（约束 C3）。
    """
    conn = open_db(settings)
    if settings.seed_on_start:
        seed_sample_data(conn)
    if index_vectors:
        backfill_vectors(settings, conn)
    return conn


def backfill_vectors(settings: Settings, conn: sqlite3.Connection) -> tuple[int, str]:
    """给整个库补齐片段向量，返回 (新写入条数, 说明)。"""
    try:
        encoder = select_encoder(settings).encoder
    except EncoderUnavailable as error:
        return 0, "编码器不可用，跳过向量回填：%s" % error
    return ensure_library_index(conn, encoder)


def seed_sample_data(conn: sqlite3.Connection) -> int:
    """写入固定样例数据；已有数据时直接返回 0。"""
    repo = MeetingRepo(conn)
    if repo.count() > 0:
        return 0
    for raw in MEETINGS:
        meeting, segments, minutes = build_models(raw)
        repo.create(meeting, segments, minutes)
    return len(MEETINGS)


def build_models(raw: dict) -> tuple[Meeting, list[Segment], Minutes]:
    """把 seeds.py 的字面量转成领域模型。

    放在这里而不是写在 seeds.py 里，是为了让种子数据本身保持「纯数据」，
    将来做数据驱动测试或换数据源都不用改模型构造逻辑。
    """
    meeting_id = raw["id"]
    participants = [
        Participant(
            id=speaker_id,
            name=SPEAKERS[speaker_id]["name"],
            role=SPEAKERS[speaker_id].get("role"),
        )
        for speaker_id in raw["participants"]
    ]
    # 参会人姓名进分词用户词典，避免「王强」被切成「王」「强」（《技术方案》9.2）
    register_words(p.name for p in participants)

    segments = [
        Segment(
            id=item["id"],
            meeting_id=meeting_id,
            start=item["start"],
            end=item["end"],
            speaker_id=item["speaker"],
            speaker_confidence=item["confidence"],
            text=item["text"],
        )
        for item in raw["segments"]
    ]
    meeting = Meeting(
        id=meeting_id,
        title=raw["title"],
        date=raw["date"],
        time=raw.get("time"),
        duration=raw["duration"],
        status=raw["status"],
        audio_file=raw.get("audio_file"),
        participants=participants,
    )
    minutes = Minutes.model_validate({"meeting_id": meeting_id, **raw["minutes"]})
    return meeting, segments, minutes
