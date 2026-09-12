"""向量 ↔ BLOB 的编解码。

M2 的声纹库与 M3 的片段向量共用同一套编码，不各写一份：
float32 小端字节流，长度恒等于 `dim * 4`，与 numpy 解耦——
仓储层因此可以在不装 numpy 的机器上单测（约束 C3）。
"""

from __future__ import annotations

import struct
from typing import Sequence


def pack_vector(vector: Sequence[float]) -> bytes:
    values = [float(value) for value in vector]
    if not values:
        raise ValueError("向量不能为空")
    return struct.pack("<%df" % len(values), *values)


def unpack_vector(blob: bytes, dim: int) -> list[float]:
    if dim <= 0:
        raise ValueError("向量维度必须为正数")
    expected = dim * 4
    if len(blob) != expected:
        raise ValueError("向量长度不符：期望 %d 字节，实际 %d" % (expected, len(blob)))
    return list(struct.unpack("<%df" % dim, blob))
