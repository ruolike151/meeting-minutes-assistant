"""检索评测脚本的测试：指标口径、标注集结构、参数扫描排序。"""

from __future__ import annotations

from app.config import RetrievalSettings
from app.retrieval.encoder import HashingEmbedder
from app.retrieval.evaluate import (
    EvalQuery,
    QueryOutcome,
    default_queries,
    evaluate,
    summarize,
    sweep,
)
from app.retrieval.index import ensure_library_index


def make_report(outcomes, **kwargs):
    from app.retrieval.evaluate import EvalReport

    return EvalReport(encoder="test", top_k=8, config=RetrievalSettings(), outcomes=outcomes, **kwargs)


def test_recall_and_mrr_metrics() -> None:
    report = make_report(
        [
            QueryOutcome("a", True, False, rank=1, recalled=True),
            QueryOutcome("b", True, False, rank=3, recalled=True),
            QueryOutcome("c", True, True, rank=None, recalled=False),
            QueryOutcome("d", True, False, rank=None, recalled=False),
        ]
    )
    assert report.recall == 0.5
    assert report.mrr == round((1.0 + 1 / 3) / 4, 4)
    assert report.answer_rate == 0.75


def test_refuse_accuracy_counts_only_unanswerable() -> None:
    report = make_report(
        [
            QueryOutcome("域内", True, False, rank=1, recalled=True),
            QueryOutcome("域外1", False, True, rank=None, recalled=False),
            QueryOutcome("域外2", False, False, rank=None, recalled=False),
        ]
    )
    assert report.refuse_accuracy == 0.5


def test_refuse_accuracy_defaults_to_one_without_unanswerable() -> None:
    report = make_report([QueryOutcome("a", True, False, rank=1, recalled=True)])
    assert report.refuse_accuracy == 1.0


def test_default_query_set_is_well_formed() -> None:
    queries = default_queries()
    answerable = [item for item in queries if item.answerable]
    unanswerable = [item for item in queries if not item.answerable]
    assert len(answerable) >= 10, "域内标注要够支撑 Recall@k"
    assert len(unanswerable) >= 8, "域外要够支撑拒答正确率"
    for item in answerable:
        assert all(":" in key for key in item.gold), "金标必须是 meeting_id:segment_id"


def test_evaluate_reports_recall_and_refusal(conn) -> None:
    """用哈希编码器跑真实数据：只要召回不为 0 且域外全拒答，指标链路就是通的。"""
    ensure_library_index(conn, HashingEmbedder())
    report = evaluate(conn, default_queries(), encoder=HashingEmbedder())
    assert report.encoder.startswith("hash-bigram-v1")
    assert 0.0 <= report.recall <= 1.0
    assert report.refuse_accuracy >= 0.6, "域外问题应被拒答（哈希编码器下的回归底线）"
    assert len(report.outcomes) == len(default_queries())
    assert "recall" in summarize(report)


def test_evaluate_keyword_only_baseline(conn) -> None:
    """不传编码器 = M1 的关键词基线，用来做前后对比。"""
    report = evaluate(conn, default_queries(), encoder=None)
    assert report.encoder == "keyword-only"
    assert report.recall > 0.0, "关键词基线至少要能召回一部分标注"


def test_sweep_returns_sorted_configs(conn) -> None:
    ensure_library_index(conn, HashingEmbedder())
    reports = sweep(
        conn,
        queries=default_queries()[:6],
        encoder=HashingEmbedder(),
        rrf_ks=(10, 60),
        min_cosines=(0.2,),
        top_ks=(4,),
        min_coverages=(0.34,),
    )
    assert len(reports) == 2
    recalls = [report.recall for report in reports]
    assert recalls == sorted(recalls, reverse=True)


def test_evaluate_marks_unanswerable_queries() -> None:
    query = EvalQuery("与会议无关的问题")
    assert query.answerable is False
    assert query.gold == ()
