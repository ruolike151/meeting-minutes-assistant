"""把真实会议语料灌进评测数据库（M5）。

为什么放在评测层而不是 `app/store/seeds.py`：

* **产品样例库 ≠ 评测语料**。前者是演示与回归用的固定数据（3 场），
  后者要能按数据集版本替换、扩充，不应该改变产品默认看到的内容；
* 评测每次运行都用**全新数据目录**，因此灌入动作是幂等的、无副作用的；
* 真实录音的转写文本来自上游标注（`samples/annotations/meeting*.demo.json`），
  经 `scripts/build_eval_corpus.py` 合成，来源可追溯。
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import date
from pathlib import Path

from app.domain.models import (
    ActionItem,
    Decision,
    Meeting,
    Minutes,
    Participant,
    Risk,
    Segment,
    SummaryTopic,
)
from app.eval.dataset import EvalDataset, RealMeeting
from app.store.repos import MeetingRepo

# 真实录音随仓库分发在 samples/audio（meeting.mp3 / meeting2.mp3）
SAMPLES_AUDIO_DIR = Path(__file__).resolve().parents[2] / "samples" / "audio"


def ingest_real_meetings(
    conn: sqlite3.Connection,
    dataset: EvalDataset,
    *,
    audio_source_dir: str | Path | None = SAMPLES_AUDIO_DIR,
    audio_target_dir: str | Path | None = None,
) -> int:
    """把语料里的真实会议写入库并回填向量无关的索引信息。返回写入场数。

    `audio_source_dir` 给定时把原声复制进评测目录：这样评测运行里的
    "证据回跳 + 播放" 也是可用的（离线样例会议没有音频文件）。
    """
    for case in dataset.meetings:
        meeting = to_meeting(case)
        segments = to_segments(case)
        minutes = to_minutes(case)
        MeetingRepo(conn).create(meeting, segments, minutes)
        _copy_audio(case, audio_source_dir, audio_target_dir)
    return len(dataset.meetings)


def to_meeting(case: RealMeeting) -> Meeting:
    return Meeting(
        id=case.meeting_id,
        title=case.title,
        date=date.fromisoformat(case.date),
        time=case.time,
        duration=case.duration,
        status="processed",
        audio_file=case.audio_file,
        participants=[
            Participant(id=item.id, name=item.name, role=item.role)
            for item in case.participants
        ],
    )


def to_segments(case: RealMeeting) -> list[Segment]:
    return [
        Segment(
            id=item.id,
            meeting_id=case.meeting_id,
            start=item.start,
            end=item.end,
            speaker_id=item.speaker_id,
            speaker_confidence=item.speaker_confidence,
            text=item.text,
        )
        for item in case.segments
    ]


def to_minutes(case: RealMeeting) -> Minutes:
    gold = case.minutes
    return Minutes(
        meeting_id=case.meeting_id,
        summary=[
            SummaryTopic(
                topic="人工撰写金标",
                points=[item.content for item in gold.decisions][:2],
                evidence=[],
            )
        ]
        if gold.decisions
        else [],
        decisions=[
            Decision(
                id="d%d" % (index + 1),
                content=item.content,
                # 人工金标自带的证据（会议内 ID），与系统产出的 ID 空间一致
                evidence=list(item.evidence),
                confidence=0.95,
            )
            for index, item in enumerate(gold.decisions)
        ],
        action_items=[
            ActionItem(
                id="a%d" % (index + 1),
                task=item.task,
                owner=item.owner,
                owner_source="explicit" if item.owner else "missing",
                due=date.fromisoformat(item.due) if item.due else None,
                status="pending",
                evidence=list(item.evidence),
                confidence=0.95,
                needs_review=False,
            )
            for index, item in enumerate(gold.action_items)
        ],
        risks=[
            Risk(id="r%d" % (index + 1), content=content, evidence=[])
            for index, content in enumerate(gold.risks)
        ],
    )


def _copy_audio(
    case: RealMeeting,
    source_dir: str | Path | None,
    target_dir: str | Path | None,
) -> None:
    if not case.audio_file or source_dir is None or target_dir is None:
        return
    source = Path(source_dir) / case.audio_file
    if not source.is_file():
        return
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target / case.audio_file)


__all__ = [
    "SAMPLES_AUDIO_DIR",
    "ingest_real_meetings",
    "to_meeting",
    "to_minutes",
    "to_segments",
]
