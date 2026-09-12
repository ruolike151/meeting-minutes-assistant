"""证据与要点判定（M5 T4）：规则版边界、LLM 判定解析、抽样可复现。"""

from __future__ import annotations

from app.eval.judge import (
    llm_support,
    parse_supported,
    rule_point_coverage,
    rule_support,
    sample_for_review,
)
from app.llm.client import LLMResult


class StubJudge:
    """按顺序返回预设判定的假客户端（不需要 Key，也不联网）。"""

    available = True
    structured_mode = "json_object"

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)

    def complete(self, messages, *, model, temperature=0.0, response_model=None, **kwargs):
        text = self.answers.pop(0)
        return LLMResult(text=text, model=model)


def test_rule_support_accepts_supporting_segment() -> None:
    supported, coverage = rule_support(
        "首页入口层级由三级简化至两级", "把入口层级从三级简化成两级，活动区域面积扩大一倍"
    )
    assert supported is True
    assert coverage > 0.5


def test_rule_support_rejects_unrelated_segment() -> None:
    """引用存在 ≠ 支撑结论：完全不相关的片段必须判为不支持。"""
    supported, coverage = rule_support("退款当天扣回积分", "今天天气不错，适合出门散步")
    assert supported is False
    assert coverage == 0.0


def test_rule_support_handles_empty_answer() -> None:
    assert rule_support("", "任何文本") == (False, 0.0)


def test_rule_point_coverage_counts_hits() -> None:
    hit, total = rule_point_coverage(
        "首页入口从三级简化成两级，活动区域面积扩大一倍",
        ["入口层级从三级简化成两级", "活动展示区域面积扩大一倍", "退款当天扣回积分"],
    )
    assert (hit, total) == (2, 3)


def test_parse_supported_tolerates_formatting() -> None:
    assert parse_supported('{"supported": true}') is True
    assert parse_supported('```json\n{"supported": false}\n```') is False
    assert parse_supported("支持") is False  # 解析不出来按不支持处理


def test_llm_support_parses_each_pair() -> None:
    client = StubJudge(['{"supported": true}', '{"supported": false}'])
    assert llm_support(client, [("结论一", "片段一"), ("结论二", "片段二")]) == [True, False]


def test_llm_support_swallows_errors() -> None:
    class Broken(StubJudge):
        def complete(self, *args, **kwargs):
            raise RuntimeError("模型超时")

    assert llm_support(Broken([]), [("结论", "片段")]) == [False]


def test_sample_for_review_is_reproducible_and_balanced() -> None:
    judgements = [True] * 20 + [False] * 3
    first = sample_for_review(judgements, per_class=5, seed=7)
    second = sample_for_review(judgements, per_class=5, seed=7)
    assert first == second
    assert len(first) == 8  # 负类只有 3 条 → 全取
    assert sum(1 for index in first if judgements[index]) == 5
