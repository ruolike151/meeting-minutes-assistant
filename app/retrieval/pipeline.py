"""检索流水线（《技术方案》9.1）。

M3 完整版：

    ① 过滤   按会议 / 发言人 / 时间区间收窄（条件全部下推到 SQL）
    ② 关键词 FTS5 BM25 召回 top-K1
    ③ 向量   查询向量 × 候选集向量 余弦召回 top-K2
    ④ 融合   RRF（只吃排名，免疫量纲差异）
    ⑤ 阈值   向量余弦 top1 与查询词覆盖率
    ⑥ 重排   M3 默认不做（决策 ③，留 M5）
    ⑦ 组装   命中片段 + 相邻片段，按预算裁剪
    ⑧ 拒答   两个信号都不达标时不调模型，直接返回「未找到」

**拒答判定用"两个信号都不过才拒答"**：查询词覆盖率与向量余弦 top1 各自
都是"有证据"的证据，任一达标就放行。这样既保住了 M1 的行为
（不向量检索时只看覆盖率），也补上了 M1 的短板——用户换个说法提问时，
字面覆盖率必然很低，但语义相似度可以救回来。

注意 RRF 分**不参与阈值判定**：它只编码排名，`1/(60+1)` 这类数值
没有跨查询可比性（详见 `fusion.py` 的说明）。

拒答仍然在调用模型之前完成：证据不达标就直接返回「未找到」，
而不是把判断权交给模型自己说不知道（约束 C4、产品文档 9 节）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from app.config import (
    MIN_COVERAGE,
    NEIGHBOR_WINDOW,
    MIN_COSINE,
    RECALL_K2,
    RRF_K,
    TOP_K,
    TOKEN_BUDGET_CHARS,
    RetrievalSettings,
)
from app.domain.models import Segment
from app.retrieval.encoder import TextEncoder
from app.retrieval.fusion import rrf_fuse
from app.retrieval.keyword import search as keyword_search
from app.retrieval.tokenize import content_tokens
from app.retrieval.vector import vector_search
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
    encoder: str | None = None          # 本次用哪一档向量（None = 没用向量）
    best_cosine: float | None = None    # 向量余弦 top1，拒答判定用
    keyword_hits: int = 0
    vector_hits: int = 0
    ranked_keys: list[tuple[str, str]] = field(default_factory=list)
    """融合后的排名（不含相邻片段），只负责"谁排第几"。

    最终 `hits` 是按时间排序的上下文（相邻片段会插进来），
    评测 Recall@k / MRR 需要的是**排名**，因此单独留一份。
    """

    @property
    def top_score(self) -> float:
        return self.hits[0].score if self.hits else 0.0

    @property
    def segment_keys(self) -> list[tuple[str, str]]:
        return [(h.segment.meeting_id, h.segment.id) for h in self.hits]

    @property
    def is_hybrid(self) -> bool:
        return self.encoder is not None


def retrieve(
    conn: sqlite3.Connection,
    query: str,
    *,
    meeting_ids: list[str] | None = None,
    speaker_ids: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    encoder: TextEncoder | None = None,
    config: RetrievalSettings | None = None,
    top_k: int = TOP_K,
    window: int = NEIGHBOR_WINDOW,
    min_coverage: float = MIN_COVERAGE,
    char_budget: int = TOKEN_BUDGET_CHARS,
) -> RetrievalResult:
    """检索并组装证据上下文。

    `encoder=None`（默认）时退化成 M1 的关键词检索，行为与 M1 完全一致：
    这样所有既有调用方与测试不需要改，也能作为参数扫描的对照基线。
    """
    settings = config or RetrievalSettings(
        top_k=top_k,
        neighbor_window=window,
        min_coverage=min_coverage,
        token_budget=char_budget,
    )
    tokens = content_tokens(query)
    if not tokens:
        return RetrievalResult(refused=True, reason="查询里没有可检索的实词")

    # ① + ②：过滤下推 + 关键词召回
    keyword_candidates = keyword_search(
        conn,
        query,
        meeting_ids=meeting_ids,
        speaker_ids=speaker_ids,
        date_from=date_from,
        date_to=date_to,
        limit=settings.recall_k1,
        tokens=tokens,
    )
    keyword_keys = [hit.key for hit in keyword_candidates]
    keyword_scores = {hit.key: hit.score for hit in keyword_candidates}

    # ③：向量召回（可选）；④：RRF 融合
    vector_hits = []
    if encoder is not None:
        try:
            vector_hits = vector_search(
                conn,
                encoder,
                query,
                meeting_ids=meeting_ids,
                speaker_ids=speaker_ids,
                date_from=date_from,
                date_to=date_to,
                limit=settings.recall_k2 or RECALL_K2,
            )
        except Exception as error:  # noqa: BLE001 - 向量这一路坏掉不该让整个检索失败
            vector_hits = []
            encoder = None
            vector_error = "%s: %s" % (type(error).__name__, error)
        else:
            vector_error = ""
    else:
        vector_error = ""
    vector_keys = [hit.key for hit in vector_hits]
    vector_scores = {hit.key: hit.score for hit in vector_hits}

    if not keyword_keys and not vector_keys:
        reason = "关键词检索没有命中任何片段"
        if vector_error:
            reason += "；向量召回失败（%s）" % vector_error
        return RetrievalResult(
            coverage=0.0,
            matched_terms=[],
            missing_terms=tokens,
            refused=True,
            reason=reason,
            encoder=encoder.storage_key if encoder else None,
            keyword_hits=0,
            vector_hits=0,
            ranked_keys=[],
        )

    fused = rrf_fuse([keyword_keys, vector_keys], k=settings.rrf_k)
    if not fused:  # 理论上不会发生：上面已保证至少一路有候选
        fused = {key: 0.0 for key in keyword_keys + vector_keys}

    segment_repo = SegmentRepo(conn)
    primary: list[RetrievedSegment] = []
    seen: set[tuple[str, str]] = set()
    for key in fused:
        meeting_id, _, segment_id = key.partition(":")
        segment = segment_repo.get(meeting_id, segment_id)
        if segment is None:
            continue
        mark = (segment.meeting_id, segment.id)
        if mark in seen:
            continue
        seen.add(mark)
        primary.append(
            RetrievedSegment(
                segment=segment,
                score=round(fused[key], 6),
                source=_source_of(key, keyword_scores, vector_scores),
            )
        )
        if len(primary) >= settings.top_k:
            break

    matched = [t for t in tokens if any(t in h.segment.text for h in primary)]
    missing = [t for t in tokens if t not in matched]
    coverage = round(len(matched) / len(tokens), 4)
    best_cosine = max(vector_scores.values()) if vector_scores else None

    # ⑤ + ⑧：两个信号都是"有证据"的证据，任一达标就放行；都不过才拒答。
    enough_by_keyword = coverage >= settings.min_coverage and bool(keyword_keys)
    enough_by_vector = best_cosine is not None and best_cosine >= settings.min_cosine
    if not enough_by_keyword and not enough_by_vector:
        if encoder is None:
            reason = "证据覆盖率 %.2f 低于阈值 %.2f，判定为检索不到" % (
                coverage,
                settings.min_coverage,
            )
        else:
            reason = (
                "证据覆盖率 %.2f 低于阈值 %.2f，且向量相似度 %.2f 低于阈值 %.2f"
                % (
                    coverage,
                    settings.min_coverage,
                    best_cosine if best_cosine is not None else 0.0,
                    settings.min_cosine,
                )
            )
        return RetrievalResult(
            hits=primary[: settings.top_k],
            coverage=coverage,
            matched_terms=matched,
            missing_terms=missing,
            refused=True,
            reason=reason,
            encoder=encoder.storage_key if encoder else None,
            best_cosine=best_cosine,
            keyword_hits=len(keyword_keys),
            vector_hits=len(vector_keys),
            ranked_keys=[(item.segment.meeting_id, item.segment.id) for item in primary],
        )

    assembled = _assemble(
        segment_repo,
        primary,
        window=settings.neighbor_window,
        char_budget=settings.token_budget,
    )
    return RetrievalResult(
        hits=assembled,
        coverage=coverage,
        matched_terms=matched,
        missing_terms=missing,
        refused=False,
        reason=vector_error,
        encoder=encoder.storage_key if encoder else None,
        best_cosine=best_cosine,
        keyword_hits=len(keyword_keys),
        vector_hits=len(vector_keys),
        ranked_keys=[(item.segment.meeting_id, item.segment.id) for item in primary],
    )


def _source_of(
    key: str, keyword_scores: dict[str, float], vector_scores: dict[str, float]
) -> str:
    """这条命中来自哪一路（两路都命中就是 both），前端与评测都看这个字段。"""
    in_keyword = key in keyword_scores
    in_vector = key in vector_scores
    if in_keyword and in_vector:
        return "both"
    return "keyword" if in_keyword else "vector"


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
