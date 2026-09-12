"""评测指标（M5 T1）：全部是纯函数，不碰网络、数据库与模型。

每个函数都在 docstring 里写清分子与分母——报告里的数字必须能被人手算复核。
分母为 0 时返回 0.0（表示"没有样本"），**不是**"效果好"；
调用方在报告里必须把这个区别写出来（《M5 计划》第 5.2 节）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


def precision_recall_f1(matched: int, predicted: int, gold: int) -> dict:
    """抽取类指标：准确率 = matched/predicted，召回 = matched/gold，F1 为调和平均。"""
    precision = matched / predicted if predicted else 0.0
    recall = matched / gold if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "matched": matched,
        "predicted": predicted,
        "gold": gold,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def recall_at_k(rank: int | None, k: int) -> float:
    """命中名次在 k 以内算 1；没命中或名次超出记 0。"""
    if rank is None or rank > k:
        return 0.0
    return 1.0


def mrr(ranks: Sequence[int | None]) -> float:
    """平均倒数排名：Σ(1/首个命中名次) / 查询数；未命中记 0。"""
    items = list(ranks)
    if not items:
        return 0.0
    total = sum(1.0 / rank for rank in items if rank)
    return round(total / len(items), 4)


def accuracy(pairs: Sequence[tuple[str, str]]) -> float:
    """完全匹配率：用于意图 / 模式 / 范围三类标签。"""
    items = list(pairs)
    if not items:
        return 0.0
    hit = sum(1 for expected, actual in items if expected == actual)
    return round(hit / len(items), 4)


def confusion_matrix(expected: Sequence[str], actual: Sequence[str]) -> dict:
    """混淆矩阵：`{期望标签: {实际标签: 条数}}`，便于看错在哪一类。"""
    matrix: dict[str, dict[str, int]] = {}
    for want, got in zip(expected, actual):
        matrix.setdefault(want, {})
        matrix[want][got] = matrix[want].get(got, 0) + 1
    return matrix


@dataclass(frozen=True)
class RefusalOutcome:
    answerable: bool     # 问题本应有答案
    refused: bool        # 系统是否拒答


def refusal_metrics(outcomes: Sequence[RefusalOutcome]) -> dict:
    """拒答的两个方向分开算，别用一个数字掩盖过度拒答：

    * 域外拒答正确率 = 正确拒答的域外数 / 域外数；
    * 域内作答率     = 正常作答的域内数 / 域内数。
    """
    items = list(outcomes)
    outside = [item for item in items if not item.answerable]
    inside = [item for item in items if item.answerable]
    return {
        "refuse_accuracy": round(
            sum(1 for item in outside if item.refused) / len(outside), 4
        )
        if outside
        else 0.0,
        "answer_rate": round(
            sum(1 for item in inside if not item.refused) / len(inside), 4
        )
        if inside
        else 0.0,
        "outside": len(outside),
        "inside": len(inside),
    }


def percentile(values: Sequence[float], p: float) -> float:
    """线性插值分位数（与 numpy 的默认口径一致，但不引入依赖）。

    口径写死：排序后取 `pos = (n-1) * p`，在相邻两点间线性插值。
    """
    items = sorted(float(value) for value in values)
    if not items:
        return 0.0
    if not 0 <= p <= 1:
        raise ValueError("p 必须在 [0, 1] 区间")
    if len(items) == 1:
        return round(items[0], 4)
    position = (len(items) - 1) * p
    low = int(position)
    high = min(low + 1, len(items) - 1)
    weight = position - low
    return round(items[low] * (1 - weight) + items[high] * weight, 4)


def cohen_kappa(first: Sequence[bool], second: Sequence[bool]) -> float:
    """两个判定者的一致率（扣掉随机一致的部分）：用于 LLM 判定与人工抽样的一致性。"""
    a = list(first)
    b = list(second)
    if len(a) != len(b) or not a:
        return 0.0
    total = len(a)
    observed = sum(1 for x, y in zip(a, b) if x == y) / total
    pa = sum(1 for x in a if x) / total
    pb = sum(1 for y in b if y) / total
    expected = pa * pb + (1 - pa) * (1 - pb)
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else 0.0
    return round((observed - expected) / (1 - expected), 4)


def aggregate_cost(traces: Sequence) -> dict:
    """成本汇总：输入 / 输出 token、费用、缓存命中率与延迟分位。

    `traces` 是 `ChatTrace`（带 `input_tokens` / `output_tokens` / `cost`，
    可选 `latency_ms`、`cached`）。
    """
    items = list(traces)
    if not items:
        return {
            "rounds": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost": 0.0,
            "cache_hit_rate": 0.0,
            "p95_latency_ms": 0.0,
        }
    input_tokens = sum(int(getattr(item, "input_tokens", 0) or 0) for item in items)
    output_tokens = sum(int(getattr(item, "output_tokens", 0) or 0) for item in items)
    cached = [item for item in items if getattr(item, "cached", False)]
    latencies = [float(getattr(item, "latency_ms", 0) or 0) for item in items]
    return {
        "rounds": len(items),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost": round(sum(float(getattr(item, "cost", 0.0) or 0.0) for item in items), 6),
        "cache_hit_rate": round(len(cached) / len(items), 4),
        "p95_latency_ms": percentile(latencies, 0.95),
    }


__all__ = [
    "RefusalOutcome",
    "accuracy",
    "aggregate_cost",
    "cohen_kappa",
    "confusion_matrix",
    "mrr",
    "percentile",
    "precision_recall_f1",
    "recall_at_k",
    "refusal_metrics",
]
