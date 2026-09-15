#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清空历史对话（保留会议库、纪要、待确认与声纹）。

面试演示前清掉自己调试留下的对话痕迹时用：

```powershell
.\\meeting_assistant\\Scripts\\python.exe scripts\\reset_conversations.py --yes
```

只删 `messages` 与 `conversations` 两张表的行（以及会话的检查点由 LangGraph 自己管理，
不影响业务数据）。不加 `--yes` 时只做预演，不写库。
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


def count(conn, table: str) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0])


def main() -> int:
    parser = argparse.ArgumentParser(description="清空历史对话")
    parser.add_argument("--yes", action="store_true", help="确认执行（否则只预演）")
    args = parser.parse_args()

    settings = Settings.from_env()
    conn = open_db(settings)
    try:
        conversations = count(conn, "conversations")
        messages = count(conn, "messages")
        if not args.yes:
            print(
                "预演：将删除 %d 条会话、%d 条消息（库：%s）；加 --yes 才真正执行"
                % (conversations, messages, settings.db_path)
            )
            return 0
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM conversations")
        conn.commit()
        print("已清空 %d 条会话、%d 条消息（会议库与声纹不受影响）" % (conversations, messages))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
