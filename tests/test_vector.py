"""片段向量仓储与向量召回测试（M3 T2/T3）。"""

from __future__ import annotations

from datetime import date

import pytest

from app.retrieval.encoder import FakeEncoder, HashingEmbedder
from app.retrieval.index import encode_segments, ensure_library_index, ensure_meeting_index
from app.retrieval.vector import cosine_scores, vector_search
from app.store.embeddings_repo import EmbeddingRepo
from app.store.repos import SegmentRepo


def test_repo_roundtrip_and_count(conn) -> None:
    repo = EmbeddingRepo(conn)
    assert repo.upsert_many(meeting_id="m_20260911", encoder="fake#2", vectors={"s1": [1.0, 0.0]}) == 1
    assert repo.count(encoder="fake#2") == 1
    assert repo.get("m_20260911", "s1", encoder="fake#2") == pytest.approx([1.0, 0.0])
    assert repo.get("m_20260911", "s1", encoder="other") is None


def test_missing_ids_is_incremental(conn) -> None:
    """回填按缺补写：第二次调用不该再返回任何片段（幂等）。"""
    repo = EmbeddingRepo(conn)
    missing = repo.missing_ids(meeting_id="m_20260911", encoder="fake#2")
    assert len(missing) == 12
    repo.upsert_many(
        meeting_id="m_20260911",
        encoder="fake#2",
        vectors={segment_id: [1.0, 0.0] for segment_id in missing},
    )
    assert repo.missing_ids(meeting_id="m_20260911", encoder="fake#2") == []


def test_prune_removes_stale_vectors(conn) -> None:
    repo = EmbeddingRepo(conn)
    repo.upsert_many(
        meeting_id="m_20260911", encoder="fake#2", vectors={"s1": [1.0], "s99": [1.0]}
    )
    removed = repo.prune(meeting_id="m_20260911", encoder="fake#2", keep_ids=["s1"])
    assert removed == 1
    assert repo.get("m_20260911", "s99", encoder="fake#2") is None


def test_ensure_meeting_index_writes_then_skips(conn) -> None:
    encoder = HashingEmbedder(dim=32)
    written, note = ensure_meeting_index(conn, encoder, "m_20260911")
    assert (written, note) == (12, "")
    assert ensure_meeting_index(conn, encoder, "m_20260911") == (0, "")


def test_ensure_library_index_covers_every_meeting(conn) -> None:
    written, note = ensure_library_index(conn, HashingEmbedder())
    assert note == ""
    assert written == 0, "bootstrap 已经建过索引，这里应该是空操作"
    assert EmbeddingRepo(conn).count(encoder="hash-bigram-v1#256") == 32


def test_encode_segments_uses_segment_ids(conn) -> None:
    segments = SegmentRepo(conn).list_for_meeting("m_20260911")[:2]
    vectors = encode_segments(HashingEmbedder(dim=16), segments)
    assert set(vectors) == {segment.id for segment in segments}


def test_candidates_respect_filters(conn) -> None:
    ensure_library_index(conn, HashingEmbedder(dim=32))
    repo = EmbeddingRepo(conn)
    encoder = "hash-bigram-v1#32"

    by_meeting = repo.candidates(encoder=encoder, meeting_ids=["m_20260909"])
    assert {item.meeting_id for item in by_meeting} == {"m_20260909"}

    by_date = repo.candidates(encoder=encoder, date_from=date(2026, 9, 10))
    assert {item.meeting_id for item in by_date} == {"m_20260911"}

    by_speaker = repo.candidates(encoder=encoder, speaker_ids=["p_wang"])
    assert by_speaker, "王强在种子数据里有发言"
    assert all(item.meeting_id for item in by_speaker)


def test_cosine_scores_orders_desc_and_validates_dim() -> None:
    ranked = cosine_scores([1.0, 0.0], [("near", [0.9, 0.1]), ("far", [0.0, 1.0])])
    assert [key for key, _ in ranked] == ["near", "far"]
    with pytest.raises(ValueError):
        cosine_scores([1.0, 0.0], [("bad", [1.0, 0.0, 0.0])])


def test_vector_search_without_index_returns_empty(conn) -> None:
    assert vector_search(conn, HashingEmbedder(dim=8), "任何问题") == []


def test_vector_search_ranks_scripted_target_first(conn) -> None:
    """用脚本化编码器构造"字面完全不重合但语义最近"的场景。"""
    target = SegmentRepo(conn).list_for_meeting("m_20260909")[1]
    query = "积分规则到底怎么算"
    encoder = FakeEncoder(
        {query: [1.0, 0.0], target.text: [1.0, 0.0]}, default=[0.0, 1.0], dim=2
    )
    ensure_meeting_index(conn, encoder, "m_20260909")

    hits = vector_search(conn, encoder, query, meeting_ids=["m_20260909"], limit=3)
    assert hits[0].segment_id == target.id
    assert hits[0].score == pytest.approx(1.0)
