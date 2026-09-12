#!/usr/bin/env python
"""演示前的缓存预热（《M3 计划》T9）。

把常见问题先跑一遍，答案落进 `llm_cache`；之后把 `MA_LLM_MODE` 设成 `cache_first`，
现场即使网络不稳也能演示（未命中的问题会明确说明需要联网，而不是静默失败）。

    $env:MA_RUN_LLM_SMOKE = "1"     # 与冒烟脚本同一道闸：没有它就不发请求
    .\meeting_assistant\Scripts\python.exe scripts\warm_cache.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.pipeline import respond  # noqa: E402
from app.config import Settings  # noqa: E402
from app.store.bootstrap import bootstrap  # noqa: E402
from scripts.eval_llm_smoke import DEMO_QUESTIONS  # noqa: E402

MEETING_ID = "m_20260911"


def main() -> int:
    settings = Settings.from_env()
    if os.environ.get("MA_RUN_LLM_SMOKE") not in ("1", "true", "yes"):
        print("未设置 MA_RUN_LLM_SMOKE=1，跳过预热（不发任何请求）。")
        return 0
    if settings.effective_llm_mode == "off":
        print("还没配置 Key（%s），无法预热。" % settings.llm_mode_reason)
        return 0

    conn = bootstrap(settings)
    for question in DEMO_QUESTIONS:
        respond(conn, question, settings=settings, active_meeting_id=MEETING_ID)
        print("已预热：%s" % question)
    count = conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
    print("\n缓存条目：%d。现场演示可用 MA_LLM_MODE=cache_first 断网跑。" % count)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
