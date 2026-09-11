"""检索流水线（《技术方案》9.1）。

M1 落地其中五步：

    1 过滤  ->  2 关键词召回  ->  5 阈值  ->  7 相邻片段组装  ->  8 拒答

向量召回（3）、RRF 融合（4）、重排（6）属于 M3。本模块刻意只依赖
「候选片段列表」这个中间结构，M3 加一路召回时下游不用改。

拒答在调用模型之前完成：证据不达标就直接返回「未找到」，
而不是把判断权交给模型自己说不知道（约束 C4、产品文档 9 节）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from app.config import (
    MIN_COVERAGE,
    NEIGHBOR_WINDOW,
    TOP_K,
    TOKEN_BUDGET_CHARS,
)
from app.domain.models import Segment
from app.retrieval.keyword import search as keyword_search
from app.retrieval.tokenize import content_tokens
from app.store.repos import SegmentRepo


@dataclass
class RetrievedSegment:
    """进入上下文的一条片段。"""

    segment: Segment
    score: float = 0.0
    source: str = "keyword"
    is_neighbor: bool = False


@dataclass
class RetrievalResult:
    hits: list[RetrievedSegment] = field(default_factory=list)
    coverage: float = 0.0
    matched_terms: list[str] = field(default_factory=list)
    missing_terms: list[str] = field(default_factory=list)
    refused: bool = True
    reason: str = ""

    @property
    def top_score(self) -> float:
        return self.hits[0].score if self.hits else 0.0

    @property
    def segment_keys(self) -> list[tuple[str, str]]:
        return [(h.segment.meeting_id, h.segment.id) for h in self.hits]


def retrieve(
    conn: sqlite3.Connection,
    query: str,
    *,
    meeting_ids: list[str] | None = None,
    speaker_ids: list[str] | None = None,
    top_k: int = TOP_K,
    window: int = NEIGHBOR_WINDOW,
    min_coverage: float = MIN_COVERAGE,
    char_budget: int = TOKEN_BUDGET_CHARS,
) -> RetrievalResult:
    """检索并组装证据上下文。"""
    tokens = content_tokens(query)
    if not tokens:
        return RetrievalResult(refused=True, reason="查询里没有可检索的实词")

    candidates = keyword_search(
        conn, query, meeting_ids=meeting_ids, speaker_ids=speaker_ids, tokens=tokens
    )
    if not candidates:
        return RetrievalResult(
            coverage=0.0, matched_terms=[], missing_terms=tokens,
            refused=True, reason="关键词检索没有命中任何片段",
        )

    segment_repo = SegmentRepo(conn)
    primary: list[RetrievedSegment] = []
    seen: set[tuple[str, str]] = set()
    for hit in candidates[: top_k * 2]:
        segment = segment_repo.get(hit.meeting_id, hit.segment_id)
        if segment is None:
            continue
        key = (segment.meeting_id, segment.id)
        if key in seen:
            continue
        seen.add(key)
        primary.append(RetrievedSegment(segment=segment, score=hit.score, source="keyword"))
        if len(primary) >= top_k:
            break

    matched = [t for t in tokens if any(t in h.segment.text for h in primary)]
    missing = [t for t in tokens if t not in matched]
    coverage = round(len(matched) / len(tokens), 4)

    if coverage < min_coverage:
        return RetrievalResult(
            hits=primary[:top_k],
            coverage=coverage,
            matched_terms=matched,
            missing_terms=missing,
            refused=True,
            reason="证据覆盖率 %.2f 低于阈值 %.2f，判定为检索不到" % (coverage, min_coverage),
        )

    assembled = _assemble(segment_repo, primary, window=window, char_budget=char_budget)
    return RetrievalResult(
        hits=assembled,
        coverage=coverage,
        matched_terms=matched,
        missing_terms=missing,
        refused=False,
        reason="",
    )


def _assemble(
    segment_repo: SegmentRepo,
    primary: list[RetrievedSegment],
    *,
    window: int,
    char_budget: int,
) -> list[RetrievedSegment]:
    """命中片段 + 相邻片段，按时间排序并控制在预算内。

    单条转写片段常常语义不完整（「那这个就下周三之前给」），
    带上相邻片段才能让合成结果不至于断章取义。
    """
    by_meeting: dict[str, list[Segment]] = {}
    picked: dict[tuple[str, str], RetrievedSegment] = {}
    neighbors: set[tuple[str, str]] = set()

    for item in primary:
        key = (item.segment.meeting_id, item.segment.id)
        picked[key] = item
        timeline = by_meeting.get(item.segment.meeting_id)
        if timeline is None:
            timeline = segment_repo.list_for_meeting(item.segment.meeting_id)
            by_meeting[item.segment.meeting_id] = timeline
        index = next((i for i, s in enumerate(timeline) if s.id == item.segment.id), None)
        if index is None:
            continue
        for offset in range(1, window + 1):
            for candidate_index in (index - offset, index + offset):
                if 0 <= candidate_index < len(timeline):
                    neighbor = timeline[candidate_index]
                    neighbors.add((neighbor.meeting_id, neighbor.id))

    for meeting_id, segment_id in neighbors:
        key = (meeting_id, segment_id)
        if key in picked:
            continue
        segment = segment_repo.get(meeting_id, segment_id)
        if segment is not None:
            picked[key] = RetrievedSegment(segment=segment, score=0.0, is_neighbor=True)

    ordered = sorted(picked.values(), key=lambda item: (item.segment.meeting_id, item.segment.start))
    budgeted: list[RetrievedSegment] = []
    used = 0
    for item in ordered:
        cost = len(item.segment.text)
        if budgeted and used + cost > char_budget:
            continue
        used += cost
        budgeted.append(item)
    return budgeted
