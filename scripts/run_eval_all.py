#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""M5 一键评测入口。

```powershell
# 离线基线（无 Key、无 GPU、无网络；默认）
.\\meeting_assistant\\Scripts\\python.exe scripts\\run_eval_all.py --offline

# 预演：只统计调用次数与 token 估算，不真的跑
.\\meeting_assistant\\Scripts\\python.exe scripts\\run_eval_all.py --live --dry-run

# 真实模型（需要 .env 里的 MA_LLM_API_KEY）
.\\meeting_assistant\\Scripts\\python.exe scripts\\run_eval_all.py --live

# 消融：再跑一组带 cross-encoder 重排的检索
.\\meeting_assistant\\Scripts\\python.exe scripts\\run_eval_all.py --offline --rerank

# 把合并报告写到指定路径（供 docs/M5-评测报告.md 使用）
.\\meeting_assistant\\Scripts\\python.exe scripts\\run_eval_all.py --offline --report docs\\M5-评测报告.md
```

离线基线是**每次都要跑**的那一组：它不依赖任何外部服务，因此报告的每个数字
都能被别人重跑出来（约束 C3）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.eval.report import render_report  # noqa: E402
from app.eval.runner import EvalBudgetExceeded, RunConfig, run, write_artifacts  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="M5 评测：跑数据集、出报告")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="离线基线（默认）")
    mode.add_argument("--cache-first", action="store_true", help="只读缓存（需要预热过）")
    mode.add_argument("--live", action="store_true", help="真实模型（需要 Key）")
    parser.add_argument("--encoder", default="auto", choices=["auto", "hash", "keyword"])
    parser.add_argument("--rerank", action="store_true", help="额外跑一组 cross-encoder 重排")
    parser.add_argument("--judge", default="rule", choices=["rule", "llm"])
    parser.add_argument("--dry-run", action="store_true", help="只估算调用量与 token")
    parser.add_argument("--dataset", default=None, help="标注集目录（默认 samples/annotations/m5）")
    parser.add_argument("--report", default=None, help="把合并报告写到该路径")
    parser.add_argument("--top-k", type=int, default=8)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_type = "live" if args.live else "cache_first" if args.cache_first else "offline"
    settings = Settings.from_env()
    dataset_path = Path(args.dataset) if args.dataset else None

    configs = [
        RunConfig(
            run_type=run_type,
            encoder=args.encoder,
            rerank="off",
            judge_mode=args.judge,
            top_k=args.top_k,
            dry_run=args.dry_run,
            label="M3 默认（RRF-only）" if not args.rerank else "RRF-only",
        )
    ]
    if args.rerank:
        configs.append(
            RunConfig(
                run_type=run_type,
                encoder=args.encoder,
                rerank="cross_encoder",
                judge_mode=args.judge,
                top_k=args.top_k,
                dry_run=args.dry_run,
                label="+ cross-encoder 重排",
            )
        )

    results = []
    for config in configs:
        try:
            result = run(settings, config, dataset_path=dataset_path)
        except EvalBudgetExceeded as error:
            print("[预算中止] %s" % error)
            return 2
        if args.dry_run:
            print(
                "[dry-run] %s：预计调用 %d 次、约 %d token"
                % (
                    config.label or config.run_type,
                    result.metrics["estimate"]["total"],
                    result.metrics["estimate"]["estimated_tokens"],
                )
            )
            results.append(result)
            continue
        run_dir = write_artifacts(result, settings.eval_output_dir / result.run_id)
        print("[完成] %s → %s" % (config.label, run_dir))
        results.append(result)

    if args.dry_run:
        return 0
    combined = render_report(results[0], history=results)
    if args.report:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(combined, encoding="utf-8")
        print("[报告] 已写入 %s" % target)
    else:
        print("")
        print(combined)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
