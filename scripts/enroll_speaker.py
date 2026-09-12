#!/usr/bin/env python
"""声纹注册 CLI（M2 T5）。

用法：

    .\meeting_assistant\Scripts\python.exe scripts\enroll_speaker.py `
      --name 项目经理 --role 项目负责人 --audio samples\audio\A_solo.mp3

不加 `--data-dir` 时用默认的 `data/`，与 Web 服务同一个库。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings                      # noqa: E402
from app.perception.enroll import enroll_speaker     # noqa: E402
from app.store.db import connect, migrate            # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="注册一个发言人的声纹")
    parser.add_argument("--name", required=True, help="发言人姓名或角色名")
    parser.add_argument("--audio", required=True, help="单人注册音频路径")
    parser.add_argument("--role", default=None, help="角色说明")
    parser.add_argument("--data-dir", default=None, help="数据目录（默认 data/）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = Settings.from_env(args.data_dir)
    settings.ensure_dirs()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        record = enroll_speaker(
            settings,
            conn,
            name=args.name,
            audio_path=args.audio,
            role=args.role,
        )
    finally:
        conn.close()
    print(
        "已注册 %s（%s）：%d 枚向量，维度 %d"
        % (record.name, record.id, record.vector_count, record.dim)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())