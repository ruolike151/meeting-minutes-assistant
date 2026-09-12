"""检索层测试：分词、关键词召回、覆盖率拒答与相邻片段组装。"""

from __future__ import annotations

from app.retrieval.encoder import FakeEncoder, HashingEmbedder
from app.retrieval.index import ensure_meeting_index
from app.retrieval.keyword import search
from app.retrieval.pipeline import retrieve
from app.retrieval.tokenize import content_tokens, match_expression, tokenize_for_index
from app.store.repos import SegmentRepo


def test_tokenize_for_index_is_space_separated() -> None:
    tokens = tokenize_for_index("首页入口太深")
    assert " " in tokens
    assert "\n" not in tokens


def test_content_tokens_drop_stopwords_and_single_chars() -> None:
    tokens = content_tokens("公司食堂今天中午吃什么？")
    assert "今天" not in tokens
    assert "中午" not in tokens
    assert "什么" not in tokens
    assert "公司" in tokens and "食堂" in tokens


def test_match_expression_escapes_quotes() -> None:
    assert match_expression(['活动', 'a"b']) == '"活动" OR "a""b"'


def test_keyword_search_ranks_activity_popup_first(conn) -> None:
    hits = search(conn, "谁提到了活动弹窗的问题？")
    assert hits
    assert hits[0].meeting_id == "m_20260911"
    assert hits[0].segment_id == "s2"


def test_keyword_search_respects_meeting_filter(conn) -> None:
    hits = search(conn, "退款积分怎么处理", meeting_ids=["m_20260909"])
    assert hits
    assert {hit.meeting_id for hit in hits} == {"m_20260909"}
    assert search(conn, "退款积分怎么处理", meeting_ids=["m_20260904"]) == []


def test_retrieve_refuses_out_of_domain_query(conn) -> None:
    """公司食堂不在会议记录里，必须拒答而不是硬答。"""
    result = retrieve(conn, "公司食堂今天中午吃什么？")
    assert result.refused is True
    assert result.coverage < 0.34
    assert "食堂" in result.missing_terms


def test_retrieve_returns_context_with_neighbors(conn) -> None:
    result = retrieve(conn, "退款之后积分怎么处理")
    assert result.refused is False
    keys = {(hit.segment.meeting_id, hit.segment.id) for hit in result.hits}
    assert ("m_20260909", "s2") in keys
    # s2 的相邻片段 s1 / s3 也要进上下文，保证语义完整
    assert ("m_20260909", "s1") in keys
    assert ("m_20260909", "s3") in keys
    assert result.matched_terms


def test_retrieve_empty_query_is_refused(conn) -> None:
    result = retrieve(conn, "？？？")
    assert result.refused is True
    assert result.hits == []


# -- M3：混合检索（向量 + RRF）------------------------------------------


def test_hybrid_can_answer_when_wording_differs(conn) -> None:
    """M1 的短板：用户换个说法，字面覆盖率必然很低，语义相似度要能救回来。"""
    target = SegmentRepo(conn).list_for_meeting("m_20260909")[1]
    query = "积分规则到底怎么算"
    encoder = FakeEncoder(
        {query: [1.0, 0.0], target.text: [1.0, 0.0]}, default=[0.0, 1.0], dim=2
    )
    ensure_meeting_index(conn, encoder, "m_20260909")

    keyword_only = retrieve(conn, query, meeting_ids=["m_20260909"])
    hybrid = retrieve(conn, query, meeting_ids=["m_20260909"], encoder=encoder)

    assert hybrid.refused is False
    assert hybrid.is_hybrid is True
    assert hybrid.encoder == "fake#2"
    assert hybrid.best_cosine == 1.0
    assert (target.meeting_id, target.id) in hybrid.segment_keys
    assert keyword_only.coverage <= hybrid.coverage or hybrid.coverage == 0.0


def test_hybrid_still_refuses_out_of_domain(conn) -> None:
    """两个信号都不达标才拒答：域外问题在混合检索下也必须拒答。"""
    encoder = HashingEmbedder(dim=64)
    ensure_meeting_index(conn, encoder, "m_20260911")
    result = retrieve(conn, "公司食堂今天中午吃什么？", encoder=encoder)
    assert result.refused is True
    assert result.best_cosine is not None
    assert "低于阈值" in result.reason


def test_hybrid_records_sources_and_counts(conn) -> None:
    encoder = HashingEmbedder(dim=64)
    ensure_meeting_index(conn, encoder, "m_20260911")
    result = retrieve(conn, "谁提到了活动弹窗的问题？", encoder=encoder)
    assert result.refused is False
    assert result.keyword_hits > 0
    sources = {hit.source for hit in result.hits}
    assert sources <= {"keyword", "vector", "both"}
    assert "keyword" in sources or "both" in sources


def test_date_filter_narrows_scope(conn) -> None:
    encoder = HashingEmbedder(dim=64)
    ensure_meeting_index(conn, encoder, "m_20260911")
    result = retrieve(
        conn,
        "活动弹窗的问题",
        encoder=encoder,
        date_from=__import__("datetime").date(2026, 9, 10),
    )
    assert result.refused is False
    assert {hit.segment.meeting_id for hit in result.hits} == {"m_20260911"}


def test_vector_failure_degrades_to_keyword(conn) -> None:
    """向量这一路坏掉不该让整个检索失败，降级到关键词并说明原因。"""

    class BrokenEncoder:
        name = "broken"
        dim = 2

        def encode(self, texts, *, batch_size: int = 16):
            raise RuntimeError("模型权重坏了")

    result = retrieve(conn, "谁提到了活动弹窗的问题？", encoder=BrokenEncoder())
    assert result.refused is False, "关键词这一路仍然能答"
    assert result.encoder is None
    assert result.keyword_hits > 0
