"""声纹库与滑窗匹配（移植自参考实现，见计划文档 v2 第 5 节）。

这一段是 M2 的技术核心，也是最需要能脱离模型单测的部分，因此刻意**不依赖
numpy / torch**：向量就是 `Sequence[float]`，所以整条匹配链路可以在没有 GPU、
没有权重、甚至没有 numpy 的机器上跑测试（约束 C3）。

四步：

1. **注册**：注册音频切窗后存下多枚向量（不是只留质心）；
2. **匹配**：与每个注册人的全部向量求余弦，取最高的 k 个求均值作为该人得分；
3. **判定**：最高分与次高分之差（margin）配合最低分阈值，决定 accepted / needs_review；
4. **合并**：相邻同说话人的窗口合并成说话人轮次。

为什么是 top-k 均值而不是质心：注册音频里有停顿、咳嗽、串音，质心会被这些
噪声拉偏；取最像的几枚向量求平均，等于只问「最像的那几次到底像不像」。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from app.perception.base import SpeakerTurn

DEFAULT_TOPK = 3
DEFAULT_MIN_SCORE = -1.0
DEFAULT_MIN_MARGIN = 0.005
DEFAULT_ACCEPT_RATIO = 0.5


def l2_normalize(vector: Sequence[float]) -> tuple[float, ...]:
    """L2 归一化；全零向量原样返回（避免除零）。"""
    norm = math.sqrt(sum(float(value) * float(value) for value in vector))
    if norm == 0.0:
        return tuple(float(value) for value in vector)
    return tuple(float(value) / norm for value in vector)


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """余弦相似度（内部各自归一化，调用方不必先处理）。"""
    if len(left) != len(right):
        raise ValueError("向量维度不一致：%d vs %d" % (len(left), len(right)))
    a = l2_normalize(left)
    b = l2_normalize(right)
    return sum(x * y for x, y in zip(a, b))


def dot_normalized(left: Sequence[float], right: Sequence[float]) -> float:
    """两个**已归一化**向量的点积 = 余弦。匹配热路径用它省一次归一化。"""
    if len(left) != len(right):
        raise ValueError("向量维度不一致：%d vs %d" % (len(left), len(right)))
    return sum(x * y for x, y in zip(left, right))


@dataclass(frozen=True)
class VoiceProfile:
    """一个注册人的声纹：多枚归一化向量 + 质心。"""

    speaker_id: str
    vectors: tuple[tuple[float, ...], ...]
    centroid: tuple[float, ...]

    @property
    def dim(self) -> int:
        return len(self.centroid)


def build_profile(speaker_id: str, vectors: Sequence[Sequence[float]]) -> VoiceProfile:
    """把注册音频的若干窗口向量收成一个声纹档案。"""
    rows = [l2_normalize(vector) for vector in vectors]
    if not rows:
        raise ValueError("注册声纹至少要有一枚向量")
    dim = len(rows[0])
    if dim == 0:
        raise ValueError("声纹向量不能为空")
    for row in rows:
        if len(row) != dim:
            raise ValueError("注册向量维度不一致：%d vs %d" % (dim, len(row)))
    centroid = l2_normalize([sum(row[i] for row in rows) / len(rows) for i in range(dim)])
    return VoiceProfile(speaker_id=speaker_id, vectors=tuple(rows), centroid=centroid)


def topk_similarity(
    profile: VoiceProfile, query: Sequence[float], k: int = DEFAULT_TOPK
) -> float:
    """注册人得分 = 与该人全部注册向量的相似度里，最高的 k 个的均值。"""
    normalized = l2_normalize(query)
    scores = sorted(
        (dot_normalized(vector, normalized) for vector in profile.vectors), reverse=True
    )
    head = scores[: max(1, min(int(k), len(scores)))]
    return sum(head) / len(head)


@dataclass(frozen=True)
class WindowPrediction:
    """一个声纹窗口的判定结果。"""

    start: float
    end: float
    speaker_id: str | None
    score: float
    margin: float
    confidence: float
    status: str                                   # accepted | needs_review
    scores: Mapping[str, float] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end - self.start


def score_to_confidence(score: float, margin: float) -> float:
    """把「相似度 + 领先优势」折算成 0-1 置信度（口径照搬参考实现）。

    相似度提供基础分，margin 提供确认项：领先得越明显，置信度越高。
    margin 用 sigmoid 压到 0-1，避免不同数据集下量纲漂移。
    """
    score_part = (score + 1.0) / 2.0
    margin_part = 1.0 / (1.0 + math.exp(-18.0 * margin))
    return max(0.0, min(1.0, 0.65 * score_part + 0.35 * margin_part))


def classify_window(
    scores: Mapping[str, float],
    *,
    start: float,
    end: float,
    min_score: float = DEFAULT_MIN_SCORE,
    min_margin: float = DEFAULT_MIN_MARGIN,
) -> WindowPrediction:
    """把「每个注册人的得分」判成一个窗口的结论。

    分低或领先不明显时标 `needs_review`：此时**保留最高分作为猜测**，
    但把状态标出来，交给下游按置信度加权、必要时进 M4 的待确认区。
    """
    if not scores:
        raise ValueError("没有可比对的声纹档案")
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_id, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else -1.0
    margin = best - second
    accepted = best >= min_score and margin >= min_margin
    return WindowPrediction(
        start=round(float(start), 3),
        end=round(float(end), 3),
        speaker_id=best_id,
        score=round(best, 4),
        margin=round(margin, 4),
        confidence=round(score_to_confidence(best, margin), 4),
        status="accepted" if accepted else "needs_review",
        scores={key: round(value, 4) for key, value in scores.items()},
    )


def match_window(
    query: Sequence[float],
    profiles: Mapping[str, VoiceProfile],
    *,
    start: float,
    end: float,
    topk: int = DEFAULT_TOPK,
    min_score: float = DEFAULT_MIN_SCORE,
    min_margin: float = DEFAULT_MIN_MARGIN,
) -> WindowPrediction | None:
    """一个窗口对所有注册人打分并判定；没有任何档案时返回 None。"""
    if not profiles:
        return None
    scores = {
        speaker_id: topk_similarity(profile, query, topk)
        for speaker_id, profile in profiles.items()
    }
    return classify_window(
        scores, start=start, end=end, min_score=min_score, min_margin=min_margin
    )


def merge_windows_to_turns(
    windows: Sequence[WindowPrediction],
    *,
    max_gap: float,
    accept_ratio: float = DEFAULT_ACCEPT_RATIO,
) -> list[SpeakerTurn]:
    """相邻同说话人的窗口合并成轮次；轮次是否可靠看 accepted 窗口的占比。"""
    if not windows:
        return []

    turns: list[SpeakerTurn] = []
    start = windows[0].start
    end = windows[0].end
    speaker_id = windows[0].speaker_id
    scores: list[float] = [windows[0].score]
    confidences: list[float] = [windows[0].confidence]
    statuses: list[str] = [windows[0].status]

    def flush() -> None:
        accepted = statuses.count("accepted") / max(1, len(statuses))
        turns.append(
            SpeakerTurn(
                start=round(start, 3),
                end=round(end, 3),
                speaker_id=speaker_id,
                confidence=round(sum(confidences) / len(confidences), 4),
                status="accepted" if accepted >= accept_ratio else "needs_review",
                scores={"avg_score": round(sum(scores) / len(scores), 4), "windows": len(scores)},
            )
        )

    for window in windows[1:]:
        same_speaker = window.speaker_id == speaker_id
        close_enough = window.start <= end + max_gap
        if same_speaker and close_enough:
            end = max(end, window.end)
            scores.append(window.score)
            confidences.append(window.confidence)
            statuses.append(window.status)
            continue
        flush()
        start, end = window.start, window.end
        speaker_id = window.speaker_id
        scores = [window.score]
        confidences = [window.confidence]
        statuses = [window.status]

    flush()
    return turns