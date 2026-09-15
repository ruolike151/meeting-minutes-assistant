#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把已有数据库里的发言人姓名改成字母代号（一次性维护脚本）。

```powershell
# 先看会改哪些（不动数据）
.\\meeting_assistant\\Scripts\\python.exe scripts\\rename_speakers.py --dry-run

# 真改
.\\meeting_assistant\\Scripts\\python.exe scripts\\rename_speakers.py
```

只改 `participants.name`（会议里的参会人显示名）与 `speakers.name`（声纹库）。
片段、纪要、证据引用、声纹向量都不动；重复执行是幂等的。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.store.db import open_db  # noqa: E402
from app.store.speaker_names import rename_speakers  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="把发言人姓名改成字母代号")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写库")
    args = parser.parse_args()

    settings = Settings.from_env()
    conn = open_db(settings)
    try:
        result = rename_speakers(conn, dry_run=args.dry_run)
        action = "将改" if args.dry_run else "已改"
        print(
            "%s：参会人姓名 %d 处，声纹库 %d 处，历史对话 %d 条（库：%s）"
            % (
                action,
                result["participants"],
                result["speakers"],
                result["messages"],
                settings.db_path,
            )
        )
        for item in result["skipped"]:
            print("  跳过：%s（%s）—— %s" % (item["id"], item["name"], item["reason"]))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
