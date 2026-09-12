"""重排（M5 T5，《技术方案》9.1 第 6 步）。

M3 把这一步留给 M5，M5 的做法是**先把消融跑出来再决定默认值**：

* `off`（默认）：RRF 直接出 top-k，行为与 M3 完全一致；
* `cross_encoder`：本地 `bge-reranker-base` 对 top-N 候选逐对打分，再取 top-k。

两条纪律：

1. **拒答口径不因重排而变。** 阈值判定仍然只看"查询词覆盖率"与"向量余弦 top1"，
   它们必须在重排之前算出来——否则换个重排模型，拒答行为就跟着漂，
   M3 用标注集扫出来的 0.50 就不再有意义。
2. **重排不可用就回落，不报错。** 权重没下载、sentence-transformers 没装、
   显存不够，都退回 RRF 顺序并把原因写进结果（与感知层"降级而非崩溃"同源）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence

from app.config import Settings
from app.retrieval.pipeline import RetrievedSegment

# 打分器签名：接收 (query, text) 对，返回相关性分数（越大越相关）。
Scorer = Callable[[Sequence[tuple[str, str]]], Sequence[float]]


class Reranker(Protocol):
    name: str
    top_n: int

    def rerank(
        self, query: str, candidates: list[RetrievedSegment], *, top_k: int
    ) -> list[RetrievedSegment]: ...


@dataclass
class NoReranker:
    """默认实现：保持 RRF 顺序（M3 行为，作为消融基线）。"""

    name: str = "off"
    top_n: int = 0

    def rerank(
        self, query: str, candidates: list[RetrievedSegment], *, top_k: int
    ) -> list[RetrievedSegment]:
        return candidates[:top_k] if top_k else list(candidates)


class CrossEncoderReranker:
    """本地 cross-encoder 重排；权重缺失时自动回落（并把原因写进 `reason`）。"""

    name = "cross_encoder"

    def __init__(
        self,
        model_dir: str | Path,
        *,
        top_n: int = 20,
        scorer: Scorer | None = None,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.top_n = max(1, int(top_n))
        self._scorer = scorer
        self.reason = "" if scorer is not None else ""

    @property
    def available(self) -> bool:
        return self._scorer is not None or self.model_dir.exists()

    def _load(self) -> Scorer | None:
        if self._scorer is not None:
            return self._scorer
        if not self.model_dir.exists():
            self.reason = "重排权重不存在：%s（已回落 RRF 顺序）" % self.model_dir
            return None
        try:
            from sentence_transformers import CrossEncoder

            model = CrossEncoder(str(self.model_dir))
        except Exception as error:  # noqa: BLE001 - 任何加载失败都回落
            self.reason = "重排模型加载失败（%s: %s），已回落 RRF 顺序" % (
                type(error).__name__,
                error,
            )
            return None

        def score(pairs: Sequence[tuple[str, str]]) -> Sequence[float]:
            return list(model.predict(list(pairs)))

        self._scorer = score
        return self._scorer

    def rerank(
        self, query: str, candidates: list[RetrievedSegment], *, top_k: int
    ) -> list[RetrievedSegment]:
        scored_input = candidates[: self.top_n]
        scorer = self._load()
        if scorer is None or not scored_input:
            return candidates[:top_k] if top_k else list(candidates)
        try:
            scores = list(scorer([(query, item.segment.text) for item in scored_input]))
        except Exception as error:  # noqa: BLE001 - 打分失败同样回落
            self.reason = "重排打分失败（%s: %s），已回落 RRF 顺序" % (
                type(error).__name__,
                error,
            )
            return candidates[:top_k] if top_k else list(candidates)
        ordered = sorted(
            zip(scored_input, scores), key=lambda pair: float(pair[1]), reverse=True
        )
        return [
            RetrievedSegment(
                segment=item.segment,
                score=round(float(value), 6),
                source="rerank",
                is_neighbor=item.is_neighbor,
            )
            for item, value in ordered[:top_k]
        ]


def build_reranker(settings: Settings | None) -> Reranker:
    """按配置返回重排器；`settings=None` 或模式为 off 时是空实现。"""
    if settings is None:
        return NoReranker()
    mode = (settings.rerank_mode or "off").lower()
    if mode != "cross_encoder":
        return NoReranker()
    return CrossEncoderReranker(
        settings.resolved_rerank_model_dir, top_n=settings.rerank_top_n
    )


__all__ = [
    "CrossEncoderReranker",
    "NoReranker",
    "Reranker",
    "Scorer",
    "build_reranker",
]
