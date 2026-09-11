"""音频与导出产物的落盘。

M1 只做两件事：把上传的音频换个安全文件名存进 data/audio/，
以及把导出文本写到 data/export/。清理策略留给部署阶段。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(name: str, *, fallback: str = "audio.bin", max_length: int = 80) -> str:
    """去掉路径分隔符与控制字符，避免上传文件名逃出目标目录。"""
    raw = unicodedata.normalize("NFKC", (name or "").strip())
    cleaned = _UNSAFE.sub("_", raw).lstrip(".")
    cleaned = cleaned.replace("..", "_")
    if not cleaned:
        return fallback
    return cleaned[:max_length]


def save_audio(data: bytes, filename: str, audio_dir: str | Path) -> Path:
    """写入上传的音频，返回落盘路径。"""
    target_dir = Path(audio_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_filename(filename, fallback="meeting_audio.bin")
    stem, suffix = target.stem, target.suffix
    index = 1
    while target.exists():
        target = target_dir / ("%s_%d%s" % (stem, index, suffix))
        index += 1
    target.write_bytes(data)
    return target


def write_export(export_dir: str | Path, filename: str, text: str) -> Path:
    """导出文本落盘（同时作为接口的响应体来源，便于排查生成结果）。"""
    target_dir = Path(export_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_filename(filename, fallback="export.txt")
    target.write_text(text, encoding="utf-8")
    return target
