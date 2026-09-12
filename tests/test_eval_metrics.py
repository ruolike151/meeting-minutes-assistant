"""指标函数（M5 T1）：用手算结果对照，保证报告里的数字可复核。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.eval.metrics import (
    RefusalOutcome,
    accuracy,
    aggregate_cost,
    cohen_kappa,
    confusion_matrix,
    mrr,
    percentile,
    precision_recall_f1,
    recall_at_k,
    refusal_metrics,
)


def test_precision_recall_f1_hand_computed() -> None:
    result = precision_recall_f1(matched=2, predicted=3, gold=5)
    assert result["precision"] == 0.6667
    assert result["recall"] == 0.4
    assert result["f1"] == 0.5


def test_precision_recall_f1_zero_denominators() -> None:
    """分母为 0 表示"没有样本"，不是"效果好"——返回 0 并在报告里写明。"""
    empty = precision_recall_f1(0, 0, 0)
    assert empty["precision"] == 0.0
    assert empty["recall"] == 0.0
    assert empty["f1"] == 0.0
    # 系统没抽出任何条目：准确率无意义（0），召回是 0
    nothing = precision_recall_f1(0, 0, 3)
    assert nothing["precision"] == 0.0
    assert nothing["recall"] == 0.0


def test_recall_at_k_boundary() -> None:
    assert recall_at_k(1, 8) == 1.0
    assert recall_at_k(8, 8) == 1.0
    assert recall_at_k(9, 8) == 0.0
    assert recall_at_k(None, 8) == 0.0


def test_mrr_hand_computed() -> None:
    assert mrr([1, 2, None]) == 0.5
    assert mrr([]) == 0.0
    assert mrr([None, None]) == 0.0


def test_accuracy_hand_computed() -> None:
    assert accuracy([("a", "a"), ("b", "c"), ("c", "c")]) == 0.6667
    assert accuracy([]) == 0.0


def test_confusion_matrix() -> None:
    matrix = confusion_matrix(["a", "a", "b"], ["a", "b", "b"])
    assert matrix == {"a": {"a": 1, "b": 1}, "b": {"b": 1}}


def test_refusal_metrics_separates_two_directions() -> None:
    """域内作答率与域外拒答率必须分开报，避免"什么都拒答"也拿满分。"""
    metrics = refusal_metrics(
        [
            RefusalOutcome(answerable=True, refused=False),
            RefusalOutcome(answerable=True, refused=True),
            RefusalOutcome(answerable=False, refused=True),
            RefusalOutcome(answerable=False, refused=False),
        ]
    )
    assert metrics["answer_rate"] == 0.5
    assert metrics["refuse_accuracy"] == 0.5
    assert metrics["inside"] == 2
    assert metrics["outside"] == 2


def test_refusal_metrics_without_samples() -> None:
    metrics = refusal_metrics([])
    assert metrics["answer_rate"] == 0.0
    assert metrics["refuse_accuracy"] == 0.0


def test_percentile_linear_interpolation() -> None:
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert percentile(values, 0.95) == 9.55
    assert percentile(values, 0.5) == 5.5
    assert percentile([7], 0.95) == 7.0
    assert percentile([], 0.95) == 0.0
    with pytest.raises(ValueError):
        percentile(values, 1.5)


def test_cohen_kappa_extremes() -> None:
    assert cohen_kappa([True, False, True], [True, False, True]) == 1.0
    assert cohen_kappa([True, False], [False, True]) == -1.0
    # 一半一致、且两类比例都是 0.5 → kappa = 0（和随机猜一样）
    assert cohen_kappa([True, True, False, False], [True, False, True, False]) == 0.0
    assert cohen_kappa([], []) == 0.0
    assert cohen_kappa([True], [True, False]) == 0.0


@dataclass
class FakeTrace:
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    latency_ms: int = 0
    cached: bool = False


def test_aggregate_cost_hand_computed() -> None:
    summary = aggregate_cost(
        [
            FakeTrace(100, 50, 0.01, latency_ms=1000),
            FakeTrace(0, 0, 0.0, latency_ms=2000, cached=True),
            FakeTrace(200, 100, 0.02, latency_ms=3000),
        ]
    )
    assert summary["rounds"] == 3
    assert summary["total_tokens"] == 450
    assert summary["cost"] == 0.03
    assert summary["cache_hit_rate"] == 0.3333
    assert summary["p95_latency_ms"] == 2900.0


def test_aggregate_cost_without_traces() -> None:
    summary = aggregate_cost([])
    assert summary["rounds"] == 0
    assert summary["total_tokens"] == 0
    assert summary["cost"] == 0.0
