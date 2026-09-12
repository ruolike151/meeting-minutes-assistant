#!/usr/bin/env python
"""用 `samples/` 里的注册音频批量建声纹库（M2）。

标签 A-D -> 角色名取自 `samples/annotations/speakers.json`，
因此"评测标注里的 A"与"声纹库里的项目经理"始终指向同一个人。

    .\meeting_assistant\Scripts\python.exe scripts\enroll_samples.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings                    # noqa: E402
from app.perception.enroll import enroll_speaker   # noqa: E402
from app.store.db import connect, migrate          # noqa: E402

MANIFEST = ROOT / "samples" / "annotations" / "speakers.json"


def load_manifest() -> list[dict]:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return list(payload["speakers"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用样例音频批量注册声纹")
    parser.add_argument("--data-dir", default=None, help="数据目录（默认 data/）")
    return parser.parse_args()


def enroll_from_manifest(settings: Settings, conn) -> list[str]:
    """按 speakers.json 注册全部样例发言人（同名覆盖），返回姓名列表。"""
    names: list[str] = []
    for item in load_manifest():
        audio = ROOT / "samples" / "audio" / item["audio"]
        record = enroll_speaker(
            settings,
            conn,
            name=item["name"],
            audio_path=audio,
            role=item.get("role"),
        )
        names.append(record.name)
        print(
            "注册 %-4s %-8s -> %s（%d 枚向量，维度 %d）"
            % (item["label"], record.name, record.id, record.vector_count, record.dim)
        )
    return names


def main() -> int:
    args = parse_args()
    settings = Settings.from_env(args.data_dir)
    settings.ensure_dirs()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        enroll_from_manifest(settings, conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())