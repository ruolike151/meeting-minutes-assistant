#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对话行为评测（M5 T6）：意图路由、范围解析、多轮上下文与拒答。

```powershell
.\\meeting_assistant\\Scripts\\python.exe scripts\\eval_dialogue.py
.\\meeting_assistant\\Scripts\\python.exe scripts\\eval_dialogue.py --live
```

输出路由准确率、三类标签的分项准确率、混淆矩阵与逐条对照（哪条错、错在哪一类）。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.eval.dataset import load_dataset  # noqa: E402
from app.eval.runner import evaluate_routes  # noqa: E402
from app.store.bootstrap import bootstrap  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="对话行为评测")
    parser.add_argument("--live", action="store_true", help="用真实模型（需要 Key）")
    parser.add_argument("--json", default=None, help="把结果写到该 JSON 文件")
    args = parser.parse_args()

    settings = Settings.from_env()
    llm_mode = "live" if args.live else "off"
    workdir = Path(tempfile.mkdtemp(prefix="ma_eval_dialogue_"))
    run_settings = replace(
        settings.for_data_dir(workdir / "data"),
        llm_mode=llm_mode,
        # 评测批量问答不挂起人工确认（否则会议停在 awaiting_review）
        review_mode="off",
    )
    conn = bootstrap(run_settings)
    try:
        dataset = load_dataset(settings.eval_dataset_dir)
        metrics, details = evaluate_routes(conn, run_settings, dataset)
    finally:
        conn.close()
    payload = {"metrics": metrics, "details": details, "llm_mode": llm_mode}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
