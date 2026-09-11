"""检索层测试：分词、关键词召回、覆盖率拒答与相邻片段组装。"""

from __future__ import annotations

from app.retrieval.keyword import search
from app.retrieval.pipeline import retrieve
from app.retrieval.tokenize import content_tokens, match_expression, tokenize_for_index


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
