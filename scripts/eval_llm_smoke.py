#!/usr/bin/env python
"""真实 LLM 冒烟（《M3 计划》T9）：跑通一次真实调用并记录 token 与费用。

默认**不跑**（要显式打开），因为 CI 与无 Key 机器上它必须不产生网络请求：

    $env:MA_RUN_LLM_SMOKE = "1"
    .\meeting_assistant\Scripts\python.exe scripts\eval_llm_smoke.py

前置：`.env` 里的 `MA_LLM_API_KEY` 已填好。没填时脚本打印原因并正常退出（退出码 0），
不会把"没配 Key"变成一次红叉。
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
from app.llm.openai_compat import price_known  # noqa: E402
from app.store.bootstrap import bootstrap  # noqa: E402
from app.store.traces_repo import TraceRepo  # noqa: E402

DEMO_QUESTIONS = [
    "谁提到了活动弹窗的问题？",
    "退款之后积分怎么处理？",
    "首屏为什么慢？",
    "公司食堂今天中午吃什么？",
]


def main() -> int:
    settings = Settings.from_env()
    if os.environ.get("MA_RUN_LLM_SMOKE") not in ("1", "true", "yes"):
        print("未设置 MA_RUN_LLM_SMOKE=1，跳过真实调用（CI 不联网）。")
        return 0
    if settings.effective_llm_mode == "off":
        print("MA_LLM_MODE=%s 解析为 off：%s" % (settings.llm_mode, settings.llm_mode_reason))
        print("把 Key 填进 %s 后再跑一次。" % (ROOT / ".env"))
        return 0

    print("模式：%s｜base_url=%s｜模型=%s"
          % (settings.effective_llm_mode, settings.llm_base_url, settings.llm_model_answer))
    if not price_known(settings.llm_model_answer, price_override=settings.llm_price_override):
        print(
            "提示：价目表里没有 %s，费用会记 0（token 照常统计）。"
            "想看到真实量级，请在 .env 里设置 MA_LLM_PRICE_INPUT / MA_LLM_PRICE_OUTPUT。"
            % settings.llm_model_answer
        )
    conn = bootstrap(settings)
    total = 0.0
    for question in DEMO_QUESTIONS:
        result = respond(conn, question, settings=settings)
        trace = TraceRepo(conn).list_recent(limit=1)[0]
        total += trace.cost
        print(
            "%-24s 形态=%-10s 引用=%d token=%d/%d 费用=%.6f 元"
            % (
                question,
                result.answer.answer_mode,
                len(result.answer.citations),
                trace.input_tokens,
                trace.output_tokens,
                trace.cost,
            )
        )
    print("\n四问合计：%.6f 元（估算值，仅供成本量级参考）" % total)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
