"""重排（M5 T5）：空实现保持顺序、注入打分器可断言排序、不可用时回落。"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from app.domain.models import Segment
from app.retrieval.pipeline import RetrievedSegment, retrieve
from app.retrieval.rerank import CrossEncoderReranker, NoReranker, build_reranker


def candidate(segment_id: str, text: str) -> RetrievedSegment:
    return RetrievedSegment(
        segment=Segment(
            id=segment_id,
            meeting_id="m_20260911",
            start=0,
            end=1,
            speaker_id=None,
            speaker_confidence=0.9,
            text=text,
        )
    )


def test_no_reranker_keeps_order() -> None:
    items = [candidate("s1", "第一名"), candidate("s2", "第二名"), candidate("s3", "第三名")]
    result = NoReranker().rerank("查询", items, top_k=2)
    assert [item.segment.id for item in result] == ["s1", "s2"]


def test_cross_encoder_reranker_reorders_by_score() -> None:
    items = [candidate("s1", "无关"), candidate("s2", "高度相关"), candidate("s3", "一般")]
    scorer = lambda pairs: [0.1, 0.9, 0.5]  # noqa: E731 - 测试里的假打分器
    reranker = CrossEncoderReranker("/nonexistent", top_n=3, scorer=scorer)
    result = reranker.rerank("查询", items, top_k=2)
    assert [item.segment.id for item in result] == ["s2", "s3"]
    assert all(item.source == "rerank" for item in result)
    assert result[0].score == 0.9


def test_cross_encoder_reranker_falls_back_without_weights(tmp_path) -> None:
    items = [candidate("s1", "一"), candidate("s2", "二")]
    reranker = CrossEncoderReranker(tmp_path / "missing", top_n=2)
    result = reranker.rerank("查询", items, top_k=2)
    assert [item.segment.id for item in result] == ["s1", "s2"]
    assert "权重不存在" in reranker.reason


def test_cross_encoder_reranker_falls_back_on_scorer_error() -> None:
    def broken(pairs):
        raise RuntimeError("显存不够")

    items = [candidate("s1", "一"), candidate("s2", "二")]
    reranker = CrossEncoderReranker("/nonexistent", top_n=2, scorer=broken)
    result = reranker.rerank("查询", items, top_k=2)
    assert [item.segment.id for item in result] == ["s1", "s2"]
    assert "打分失败" in reranker.reason


def test_build_reranker_respects_mode(settings) -> None:
    assert build_reranker(None).name == "off"
    assert build_reranker(settings).name == "off"
    enabled = build_reranker(replace(settings, rerank_mode="cross_encoder", rerank_top_n=7))
    assert enabled.name == "cross_encoder"
    assert enabled.top_n == 7


def test_default_rerank_path_points_into_model_dir(tmp_path) -> None:
    """"默认权重路径必须是 <项目根>/model/bge-reranker-base。

    写成相对路径 "bge-reranker-base" 会让 resolved 落到项目根下，
    权重明明下载了却静默回落——消融表会出现两行完全一样的数字（踩过这个坑）。
    """
    from app.config import Settings

    fresh = Settings.from_env(tmp_path / "data")
    assert fresh.resolved_rerank_model_dir.name == "bge-reranker-base"
    assert fresh.resolved_rerank_model_dir.parent.name == "model"


def test_pipeline_uses_reranker_and_keeps_refusal_signal(conn, settings) -> None:
    """重排改变排名，但不改变拒答：域外问题在两种模式下都必须拒答。"""
    baseline = retrieve(conn, "设计稿什么时候交付", config=settings.retrieval)
    reversed_reranker = CrossEncoderReranker(
        "/nonexistent",
        top_n=20,
        # 分数递增 → 排序结果与 RRF 顺序相反（用来断言"重排真的生效了"）
        scorer=lambda pairs: list(range(1, len(pairs) + 1)),
    )
    reranked = retrieve(
        conn,
        "设计稿什么时候交付",
        config=settings.retrieval,
        reranker=reversed_reranker,
    )
    assert reranked.reranked is True
    assert reranked.rerank_candidates >= len(reranked.ranked_keys)
    assert reranked.ranked_keys != baseline.ranked_keys
    # 拒答仍然由覆盖率 / 余弦决定，与重排无关
    assert reranked.refused == baseline.refused
    assert reranked.coverage == baseline.coverage

    outside = "公司食堂今天中午吃什么？"
    assert retrieve(conn, outside, config=settings.retrieval).refused is True
    assert (
        retrieve(
            conn, outside, config=settings.retrieval, reranker=reversed_reranker
        ).refused
        is True
    )


def test_pipeline_marks_rerank_reason_when_unavailable(conn, settings) -> None:
    reranker = CrossEncoderReranker("/nonexistent/model", top_n=20)
    result = retrieve(
        conn, "设计稿什么时候交付", config=settings.retrieval, reranker=reranker
    )
    assert "权重不存在" in result.rerank_reason
