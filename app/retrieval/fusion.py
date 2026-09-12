"""RRF 融合（《技术方案》9.1 第 4 步）。

为什么用 RRF 而不是加权求和：BM25 的分值与余弦相似度量纲不可比，
加权求和需要先做分数归一化，而归一化在不同查询上并不稳定
（同一个分数在这条查询上算高、在那条查询上算低）。
RRF 只用**排名**：`score = Σ weight_i / (k + rank_i)`，天然免疫量纲问题。

对应地，**RRF 分不能当绝对阈值用**——它只编码"被几个召回列表排在第几"，
`1/(60+1) ≈ 0.0164` 这类数值没有跨查询可比性。拒答阈值因此放在
两个可解释量上：查询词覆盖率与向量余弦 top1（见 `pipeline.py`）。
"""

from __future__ import annotations

from typing import Sequence


def rrf_fuse(
    rankings: Sequence[Sequence[str]],
    *,
    k: int = 60,
    weights: Sequence[float] | None = None,
) -> dict[str, float]:
    """融合多路排名，返回 `key -> 融合分`（按分降序，便于直接取 top-k）。

    `rankings` 是每路的 key 列表，**从最相关到最不相关**；`k` 是 RRF 常数
    （默认 60，原论文的经验值，也是《技术方案》9.1 的初始参数）。
    """
    if k < 0:
        raise ValueError("RRF 常数不能为负")
    if weights is not None and len(weights) != len(rankings):
        raise ValueError("weights 数量必须与排名路数一致")

    scores: dict[str, float] = {}
    for index, ranking in enumerate(rankings):
        weight = 1.0 if weights is None else float(weights[index])
        for position, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + weight / (k + position + 1)
    # 同分时按 key 排序，保证结果确定（否则测试与评测会随字典序漂移）
    return dict(sorted(scores.items(), key=lambda item: (-item[1], item[0])))
