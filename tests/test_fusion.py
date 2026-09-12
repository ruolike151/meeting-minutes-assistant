"""RRF 融合测试：排序确定、权重生效、非法输入报错。"""

from __future__ import annotations

import pytest

from app.retrieval.fusion import rrf_fuse


def test_document_in_both_lists_wins() -> None:
    fused = rrf_fuse([["a", "b", "c"], ["c", "d"]])
    assert list(fused)[0] == "c", "两路都命中的候选应该排最前"


def test_single_list_keeps_original_order() -> None:
    fused = rrf_fuse([["a", "b", "c"]])
    assert list(fused) == ["a", "b", "c"]
    assert fused["a"] > fused["b"] > fused["c"]


def test_weights_shift_ranking() -> None:
    balanced = rrf_fuse([["a"], ["b"]])
    assert balanced["a"] == balanced["b"]
    weighted = rrf_fuse([["a"], ["b"]], weights=[3.0, 1.0])
    assert weighted["a"] > weighted["b"]


def test_ties_are_broken_deterministically() -> None:
    first = rrf_fuse([["b", "a"], ["a", "b"]])
    second = rrf_fuse([["b", "a"], ["a", "b"]])
    assert list(first) == list(second)
    assert first["a"] == first["b"]


def test_empty_rankings() -> None:
    assert rrf_fuse([]) == {}
    assert rrf_fuse([[], []]) == {}


def test_rejects_invalid_arguments() -> None:
    with pytest.raises(ValueError):
        rrf_fuse([["a"]], k=-1)
    with pytest.raises(ValueError):
        rrf_fuse([["a"], ["b"]], weights=[1.0])
