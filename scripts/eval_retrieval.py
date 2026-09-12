#!/usr/bin/env python
"""检索效果评测 CLI（《M3 计划》T8）。

评测逻辑在 `app/retrieval/evaluate.py`（可单测的库函数），这里只是薄壳：
选编码器 → 跑标注集 → 打表 → 落 JSON。

用法：

    # 关键词基线（M1 的行为）
    .\meeting_assistant\Scripts\python.exe scripts\eval_retrieval.py --encoder keyword

    # 混合检索（本地 BGE）
    .\meeting_assistant\Scripts\python.exe scripts\eval_retrieval.py --encoder bge

    # 参数扫描：按 (召回, MRR, 拒答正确率) 排序输出
    .\meeting_assistant\Scripts\python.exe scripts\eval_retrieval.py --encoder bge --sweep

结果默认写 `data/eval/retrieval_metrics.json`（`--json` 可覆盖）。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.retrieval.encoder import EncoderUnavailable, select_encoder  # noqa: E402
from app.retrieval.evaluate import (  # noqa: E402
    default_queries,
    evaluate,
    summarize,
    sweep,
)
from app.store.bootstrap import bootstrap  # noqa: E402

ENCODER_CHOICES = ("keyword", "hash", "bge", "api", "auto")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检索层评测：Recall@k / MRR / 拒答正确率")
    parser.add_argument("--encoder", default="auto", choices=ENCODER_CHOICES)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--sweep", action="store_true", help="扫参并输出对比表")
    parser.add_argument("--top", type=int, default=10, help="扫参时打印前 N 组")
    parser.add_argument("--json", default=None, help="结果落盘路径")
    parser.add_argument("--data-dir", default=None, help="数据目录（默认 data/）")
    return parser.parse_args()


def resolve_encoder(settings: Settings, choice: str):
    if choice == "keyword":
        return None, "keyword-only"
    settings = replace(settings, text_encoder=choice)
    try:
        selection = select_encoder(settings)
    except EncoderUnavailable as error:
        print("编码器不可用：%s" % error)
        print("（可用 --encoder hash 走零依赖兜底，或先跑 download_models.py --embedding-only）")
        raise SystemExit(2) from error
    if selection.is_fallback:
        print("提示：%s" % selection.fallback_reason)
    return selection.encoder, selection.encoder.storage_key


def main() -> int:
    args = parse_args()
    settings = Settings.from_env(args.data_dir)
    # 索引要与评测用的编码器一致：否则向量这一路查不到任何候选，
    # 评测会静默退化成关键词基线（这种"假对比"比没有对比更糟）。
    index_settings = replace(
        settings, text_encoder="hash" if args.encoder == "keyword" else args.encoder
    )
    conn = bootstrap(index_settings)
    encoder, label = resolve_encoder(settings, args.encoder)

    queries = default_queries()
    print("标注集：%d 条（域内 %d / 域外 %d）"
          % (len(queries), sum(1 for q in queries if q.answerable),
             sum(1 for q in queries if not q.answerable)))

    if args.sweep:
        reports = sweep(conn, queries, encoder=encoder, base=settings.retrieval)
        print("\n参数扫描（按召回 / MRR / 拒答排序，前 %d 组）：" % args.top)
        for report in reports[: args.top]:
            print("  " + summarize(report))
        payload = {"mode": "sweep", "encoder": label,
                   "reports": [report.to_dict() for report in reports[: args.top]]}
    else:
        report = evaluate(conn, queries, encoder=encoder, top_k=args.top_k)
        print("\n" + summarize(report))
        print("\n域内未命中：")
        missed = [item for item in report.outcomes if item.answerable and not item.recalled]
        print("  " + ("无" if not missed else "\n  ".join(item.query for item in missed)))
        print("域外漏答（应当拒答却没拒）：")
        leaked = [item for item in report.outcomes if not item.answerable and not item.refused]
        print("  " + ("无" if not leaked else "\n  ".join(item.query for item in leaked)))
        payload = report.to_dict()

    target = Path(args.json) if args.json else settings.data_dir / "eval" / "retrieval_metrics.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n已写入：%s" % target)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
