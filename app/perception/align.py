"""身份对齐：把「转写片段」和「声纹窗口」合成入库片段（M2 T5）。

一条转写片段常常横跨多个声纹窗口，因此按「重叠时长 × 窗口置信度」加权投票。
没有任何窗口覆盖的片段（纯静音、音乐、或者窗口全被判为待复核）标成未识别，
不硬塞一个身份——这是产品文档「无证据则明说」的原则在感知层的同一条落点。

两个函数都是纯函数：给同一组输入必然得到同一组输出，因此可以完全离线单测。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.domain.models import Segment
from app.perception.base import RawSegment
from app.perception.voiceref import WindowPrediction

DEFAULT_ACCEPT_RATIO = 0.5


@dataclass(frozen=True)
class VoteResult:
    speaker_id: str | None
    confidence: float
    status: str


def vote_speaker_for_span(
    start: float,
    end: float,
    windows: Sequence[WindowPrediction],
    *,
    accept_ratio: float = DEFAULT_ACCEPT_RATIO,
) -> VoteResult:
    """按重叠加权投票决定一条片段属于谁。"""
    votes: dict[str, float] = {}
    confidences: dict[str, list[float]] = {}
    statuses: dict[str, list[str]] = {}

    for window in windows:
        overlap = max(0.0, min(end, window.end) - max(start, window.start))
        if overlap <= 0 or window.speaker_id is None:
            continue
        weight = overlap * max(0.01, window.confidence)
        votes[window.speaker_id] = votes.get(window.speaker_id, 0.0) + weight
        confidences.setdefault(window.speaker_id, []).append(window.confidence)
        statuses.setdefault(window.speaker_id, []).append(window.status)

    if not votes:
        return VoteResult(speaker_id=None, confidence=0.0, status="needs_review")

    winner = max(votes.items(), key=lambda item: item[1])[0]
    confidence = sum(confidences[winner]) / len(confidences[winner])
    accepted = statuses[winner].count("accepted") / max(1, len(statuses[winner]))
    return VoteResult(
        speaker_id=winner,
        confidence=round(confidence, 4),
        status="accepted" if accepted >= accept_ratio else "needs_review",
    )


def align_segments(
    segments: Sequence[RawSegment],
    windows: Sequence[WindowPrediction],
    *,
    meeting_id: str,
    id_prefix: str = "s",
    start_index: int = 1,
) -> list[Segment]:
    """转写片段 × 声纹窗口 -> 带说话人与置信度的领域片段。"""
    aligned: list[Segment] = []
    for offset, segment in enumerate(segments):
        vote = vote_speaker_for_span(segment.start, segment.end, windows)
        aligned.append(
            Segment(
                id="%s%d" % (id_prefix, start_index + offset),
                meeting_id=meeting_id,
                start=segment.start,
                end=segment.end,
                speaker_id=vote.speaker_id,
                speaker_confidence=vote.confidence,
                text=segment.text,
            )
        )
    return aligned