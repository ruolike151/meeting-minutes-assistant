#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""纪要质量评测（M5 T3）：与人工金标比对，输出 Schema / 待办 / 结论 / 负责人四项指标。

```powershell
.\\meeting_assistant\\Scripts\\python.exe scripts\\eval_minutes.py
.\\meeting_assistant\\Scripts\\python.exe scripts\\eval_minutes.py --live   # 需要 Key
```

默认离线（`MA_LLM_MODE=off`）：此时纪要来自固定备用内容，指标只证明匹配逻辑正确。
真实质量数字要加 `--live`（或在 `run_eval_all.py` 里一起跑）。
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
from app.eval.runner import evaluate_minutes  # noqa: E402
from app.store.bootstrap import bootstrap  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="纪要质量评测")
    parser.add_argument("--live", action="store_true", help="用真实模型（需要 Key）")
    parser.add_argument("--json", default=None, help="把结果写到该 JSON 文件")
    args = parser.parse_args()

    settings = Settings.from_env()
    llm_mode = "live" if args.live else "off"
    workdir = Path(tempfile.mkdtemp(prefix="ma_eval_minutes_"))
    run_settings = replace(settings.for_data_dir(workdir / "data"), llm_mode=llm_mode)
    conn = bootstrap(run_settings)
    try:
        dataset = load_dataset(settings.eval_dataset_dir)
        metrics, details = evaluate_minutes(
            conn, run_settings, dataset, offline=not args.live
        )
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
