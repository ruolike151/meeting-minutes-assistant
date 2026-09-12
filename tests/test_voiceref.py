"""声纹匹配核心算法（M2 T4）。

全部用**合成向量**：无模型、无 GPU、无音频文件，也不需要 numpy。
构造方式是「每人一个正交基向量 + 加噪」，这样"同人"与"异人"的相似度
在数学上是可预期的，阈值行为可以被精确断言。
"""

from __future__ import annotations

import random

import pytest

from app.perception.voiceref import (
    WindowPrediction,
    build_profile,
    classify_window,
    cosine,
    l2_normalize,
    match_window,
    merge_windows_to_turns,
    score_to_confidence,
    topk_similarity,
)

DIM = 8


def unit(index: int) -> list[float]:
    vector = [0.0] * DIM
    vector[index] = 1.0
    return vector


def noisy(base: list[float], *, noise: float = 0.08, seed: int = 0) -> list[float]:
    rnd = random.Random(seed)
    return [value + rnd.uniform(-noise, noise) for value in base]


def norm(vector) -> float:
    return sum(value * value for value in vector) ** 0.5


def test_l2_normalize_gives_unit_vector_and_handles_zero() -> None:
    assert norm(l2_normalize([3.0, 4.0])) == pytest.approx(1.0)
    assert l2_normalize([0.0, 0.0]) == (0.0, 0.0)


def test_cosine_bounds_and_dimension_check() -> None:
    assert cosine(unit(0), unit(0)) == pytest.approx(1.0)
    assert cosine(unit(0), unit(1)) == pytest.approx(0.0)
    assert cosine([2.0, 0.0], [5.0, 0.0]) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        cosine([1.0, 0.0], [1.0, 0.0, 0.0])


def test_build_profile_rejects_empty_and_ragged_input() -> None:
    with pytest.raises(ValueError):
        build_profile("spk_a", [])
    with pytest.raises(ValueError):
        build_profile("spk_a", [[1.0, 0.0], [1.0, 0.0, 0.0]])


def test_build_profile_centroid_is_normalized() -> None:
    profile = build_profile("spk_a", [noisy(unit(0), seed=1), noisy(unit(0), seed=2)])
    assert profile.dim == DIM
    assert norm(profile.centroid) == pytest.approx(1.0)
    assert len(profile.vectors) == 2


def test_topk_similarity_prefers_same_speaker_and_ignores_outliers() -> None:
    """同人应该拿到高分；注册向量里的"异类"不应该拉低得分。"""
    profile = build_profile(
        "spk_a",
        [noisy(unit(0), seed=1), noisy(unit(0), seed=2), noisy(unit(0), seed=3), unit(3)],
    )
    same = topk_similarity(profile, noisy(unit(0), seed=9), k=3)
    other = topk_similarity(profile, unit(5), k=3)
    assert same > 0.9
    assert other < 0.3
    # k=3 时那枚正交向量被排除，得分高于把 4 枚全算进去
    assert same > topk_similarity(profile, noisy(unit(0), seed=9), k=4)


def test_score_to_confidence_grows_with_score_and_margin() -> None:
    low = score_to_confidence(0.3, 0.0)
    high = score_to_confidence(0.9, 0.0)
    assert high > low
    assert score_to_confidence(0.9, 0.5) > score_to_confidence(0.9, 0.001)


def test_classify_window_accepts_clear_winner() -> None:
    prediction = classify_window(
        {"spk_a": 0.92, "spk_b": 0.31}, start=0.0, end=2.5
    )
    assert prediction.speaker_id == "spk_a"
    assert prediction.status == "accepted"
    assert 0.0 <= prediction.confidence <= 1.0


def test_classify_window_flags_near_tie_but_keeps_guess() -> None:
    """领先不明显时保留最高分作为猜测，但状态标为待复核。"""
    prediction = classify_window({"spk_a": 0.61, "spk_b": 0.609}, start=0.0, end=2.5)
    assert prediction.status == "needs_review"
    assert prediction.speaker_id == "spk_a"


def test_match_window_picks_the_registered_speaker() -> None:
    profiles = {
        "spk_a": build_profile("spk_a", [noisy(unit(0), seed=1), noisy(unit(0), seed=2)]),
        "spk_b": build_profile("spk_b", [noisy(unit(1), seed=3), noisy(unit(1), seed=4)]),
    }
    prediction = match_window(noisy(unit(1), seed=7), profiles, start=0.0, end=2.5)
    assert prediction is not None
    assert prediction.speaker_id == "spk_b"
    assert prediction.status == "accepted"


def test_match_window_without_profiles_returns_none() -> None:
    assert match_window(unit(0), {}, start=0.0, end=2.5) is None


def win(start: float, end: float, speaker: str, confidence: float = 0.9, status: str = "accepted") -> WindowPrediction:
    return WindowPrediction(
        start=start,
        end=end,
        speaker_id=speaker,
        score=0.8,
        margin=0.2,
        confidence=confidence,
        status=status,
        scores={speaker: 0.8},
    )


def test_merge_windows_to_turns_joins_same_speaker() -> None:
    turns = merge_windows_to_turns(
        [win(0.0, 2.5, "spk_a"), win(1.25, 3.75, "spk_a"), win(3.75, 6.25, "spk_b")],
        max_gap=1.25,
    )
    assert [(turn.speaker_id, turn.start, turn.end) for turn in turns] == [
        ("spk_a", 0.0, 3.75),
        ("spk_b", 3.75, 6.25),
    ]


def test_merge_windows_marks_mostly_uncertain_turn_as_review() -> None:
    turns = merge_windows_to_turns(
        [
            win(0.0, 2.5, "spk_a", 0.4, "needs_review"),
            win(1.25, 3.75, "spk_a", 0.4, "needs_review"),
            win(2.5, 5.0, "spk_a", 0.9, "accepted"),
        ],
        max_gap=1.25,
    )
    assert len(turns) == 1
    assert turns[0].status == "needs_review"
    assert turns[0].confidence < 0.8