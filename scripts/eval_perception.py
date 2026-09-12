#!/usr/bin/env python
"""感知层评测（M2 T8）。

对指定会议跑一遍真实链路，再与 `samples/annotations/` 的人工标注比对，输出：

* 窗口级说话人准确率（与参考实现同口径：排除 GROUP）
* 每个说话人的召回率 + 混淆矩阵
* 片段级说话人准确率（按重叠加权投票）
* 转写 CER / WER（以人工标注正文为基准）
* 端到端 RTF、参数与设备信息

注意：标注**不喂给模型**，只用于比对——衡量的是自动链路的真实水平。

    .\meeting_assistant\Scripts\python.exe scripts\eval_perception.py `
      --meeting samples\audio\meeting.mp3 `
      --annotation samples\annotations\meeting.demo.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings                          # noqa: E402
from app.perception import align as align_module         # noqa: E402
from app.perception import audio_io                      # noqa: E402
from app.perception.local import LocalPerception         # noqa: E402
from app.store.db import connect, migrate                 # noqa: E402
from scripts.enroll_samples import enroll_from_manifest  # noqa: E402

GROUP = "GROUP"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="感知层评测")
    parser.add_argument("--meeting", default=str(ROOT / "samples" / "audio" / "meeting.mp3"))
    parser.add_argument(
        "--annotation",
        default=str(ROOT / "samples" / "annotations" / "meeting.demo.json"),
    )
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--report", default=str(ROOT / "docs" / "感知层评测报告.md"))
    parser.add_argument("--metrics", default=str(ROOT / "model" / "perception_metrics.json"))
    parser.add_argument("--skip-enroll", action="store_true", help="复用已有声纹库")
    return parser.parse_args()


def overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def load_annotation(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    segments = [
        {
            "label": item["speaker"],
            "start": float(item["start"]),
            "end": float(item["end"]),
            "text": str(item["text"]),
        }
        for item in payload["segments"]
    ]
    return {"raw": payload, "segments": segments}


def name_to_label() -> dict[str, str]:
    manifest = json.loads(
        (ROOT / "samples" / "annotations" / "speakers.json").read_text(encoding="utf-8")
    )
    return {item["name"]: item["label"] for item in manifest["speakers"]}


def label_for(predicted, names: dict[str, str], mapping: dict[str, str]) -> str | None:
    if not predicted:
        return None
    return mapping.get(names.get(predicted, predicted))


def ground_truth_for(start: float, end: float, annotation: dict) -> tuple[str | None, float]:
    best_label, best_overlap = None, 0.0
    for item in annotation["segments"]:
        value = overlap(start, end, item["start"], item["end"])
        if value > best_overlap:
            best_label, best_overlap = item["label"], value
    return best_label, best_overlap


def evaluate_windows(windows, annotation, names, mapping) -> dict:
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    total = correct = 0
    group_windows = 0
    per_speaker: dict[str, dict[str, int]] = defaultdict(lambda: {"correct": 0, "total": 0})

    for window in windows:
        truth, covered = ground_truth_for(window.start, window.end, annotation)
        if truth is None or covered < (window.end - window.start) * 0.5:
            continue
        if truth == GROUP:
            group_windows += 1
            continue
        predicted = label_for(window.speaker_id, names, mapping) or "UNKNOWN"
        confusion[truth][predicted] += 1
        per_speaker[truth]["total"] += 1
        total += 1
        if predicted == truth:
            correct += 1
            per_speaker[truth]["correct"] += 1

    return {
        "windows_scored": total,
        "group_windows_skipped": group_windows,
        "window_accuracy": correct / total if total else 0.0,
        "macro_recall": (
            sum(item["correct"] / item["total"] for item in per_speaker.values() if item["total"])
            / max(1, len([1 for item in per_speaker.values() if item["total"]]))
        ),
        "per_speaker": {key: dict(value) for key, value in sorted(per_speaker.items())},
        "confusion": {key: dict(value) for key, value in sorted(confusion.items())},
    }


def evaluate_segments(segments, annotation, names, mapping) -> dict:
    total = correct = 0
    details = []
    for item in annotation["segments"]:
        if item["label"] == GROUP:
            continue
        votes: dict[str, float] = defaultdict(float)
        for segment in segments:
            value = overlap(item["start"], item["end"], segment.start, segment.end)
            if value <= 0:
                continue
            label = label_for(segment.speaker_id, names, mapping) or "UNKNOWN"
            votes[label] += value * max(0.01, segment.speaker_confidence)
        predicted = max(votes.items(), key=lambda pair: pair[1])[0] if votes else "UNKNOWN"
        ok = predicted == item["label"]
        total += 1
        correct += 1 if ok else 0
        details.append(
            {
                "start": item["start"],
                "end": item["end"],
                "truth": item["label"],
                "predicted": predicted,
                "ok": ok,
            }
        )
    return {
        "segments_scored": total,
        "segment_accuracy": correct / total if total else 0.0,
        "details": details,
    }


def text_metrics(segments, annotation) -> dict:
    reference = "".join(item["text"] for item in annotation["segments"])
    hypothesis = "".join(segment.text for segment in segments)
    try:
        import jiwer
    except ImportError:
        return {"cer": None, "wer": None, "note": "未安装 jiwer，跳过文本指标"}
    return {
        "cer": float(jiwer.cer(reference, hypothesis)),
        "wer": float(jiwer.wer(reference, hypothesis)),
        "reference_chars": len(reference),
        "hypothesis_chars": len(hypothesis),
    }


def write_report(path: Path, payload: dict, segments, names, mapping) -> None:
    metrics = payload["metrics"]
    lines = [
        "# 感知层评测报告（M2）",
        "",
        "由 `scripts/eval_perception.py` 生成；标注只用于比对，不参与推理。",
        "",
        "## 运行参数",
        "",
        "| 项 | 值 |",
        "| --- | --- |",
        "| 会议音频 | `%s` |" % payload["meeting"],
        "| 人工标注 | `%s` |" % payload["annotation"],
        "| 生成时间 | %s |" % payload["generated_at"],
        "| 转写模型 | `%s`（%s） |" % (payload["model"], payload["device"]),
        "| 声纹编码器 | `%s` |" % payload["encoder"],
        "| 滑窗 / 步长 | %.2f s / %.2f s |" % (payload["window"], payload["hop"]),
        "| top-k / 最低分 / 最低 margin | %d / %.3f / %.4f |"
        % (payload["topk"], payload["min_score"], payload["min_margin"]),
        "| 声纹库人数 | %d |" % payload["speakers"],
        "",
        "## 说话人识别",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        "| 窗口级准确率（排除 GROUP） | %.4f |" % metrics["window"]["window_accuracy"],
        "| 宏平均召回率 | %.4f |" % metrics["window"]["macro_recall"],
        "| 参与评分的窗口数 | %d |" % metrics["window"]["windows_scored"],
        "| 跳过的 GROUP 窗口 | %d |" % metrics["window"]["group_windows_skipped"],
        "| 片段级准确率 | %.4f |" % metrics["segment"]["segment_accuracy"],
        "",
        "### 每人召回率",
        "",
        "| 说话人 | 正确 | 总数 | 召回率 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for speaker, value in metrics["window"]["per_speaker"].items():
        recall = value["correct"] / value["total"] if value["total"] else 0.0
        lines.append("| %s | %d | %d | %.4f |" % (speaker, value["correct"], value["total"], recall))

    lines.extend(["", "### 混淆矩阵（行=人工标注，列=自动判定）", "", "| 真值 \\ 预测 | " + " | ".join(
        sorted({key for row in metrics["window"]["confusion"].values() for key in row})
    ) + " |", "| --- |" + " ---: |" * len(sorted({key for row in metrics["window"]["confusion"].values() for key in row}))])
    columns = sorted({key for row in metrics["window"]["confusion"].values() for key in row})
    for truth, row in metrics["window"]["confusion"].items():
        lines.append("| %s | " % truth + " | ".join(str(row.get(col, 0)) for col in columns) + " |")

    text = metrics["text"]
    lines.extend(["", "## 转写质量", "", "| 指标 | 数值 |", "| --- | ---: |"])
    if text.get("cer") is None:
        lines.append("| CER / WER | %s |" % text.get("note", "不可用"))
    else:
        lines.append("| CER | %.4f |" % text["cer"])
        lines.append("| WER | %.4f |" % text["wer"])

    lines.extend(
        [
            "",
            "## 效率",
            "",
            "| 指标 | 数值 |",
            "| --- | ---: |",
            "| 音频时长（秒） | %.2f |" % payload["audio_seconds"],
            "| 端到端耗时（秒） | %.2f |" % payload["elapsed_seconds"],
            "| RTF | %.4f |" % payload["rtf"],
            "",
            "## 片段明细",
            "",
            "| 时间 | 说话人 | 置信度 | 文本 |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for segment in segments:
        name = names.get(segment.speaker_id or "", "未识别")
        lines.append(
            "| %.2f-%.2f | %s | %.2f | %s |"
            % (segment.start, segment.end, name, segment.speaker_confidence, segment.text)
        )
    lines.extend(
        [
            "",
            "## 已知偏差",
            "",
            "* GROUP（多人合说）没有对应的领域字段，评测时排除在准确率之外，单独计数；",
            "* 片段级准确率按重叠时长加权投票，片段边界与人工标注不完全一致时会引入误差；",
            "* CER/WER 以整场拼接文本计算，不含标点归一化，属于偏保守的估计。",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    settings = Settings.from_env(args.data_dir)
    settings.ensure_dirs()
    meeting_path = Path(args.meeting)
    annotation_path = Path(args.annotation)
    annotation = load_annotation(annotation_path)
    mapping = name_to_label()

    conn = connect(settings.db_path)
    try:
        migrate(conn)
        if not args.skip_enroll:
            enroll_from_manifest(settings, conn)
        backend = LocalPerception.from_settings(settings, conn)
        if not backend.profiles:
            print("声纹库为空：请先跑 scripts/enroll_samples.py 或去掉 --skip-enroll")
            return 2

        started = time.perf_counter()
        audio = audio_io.normalize(meeting_path, settings.perception_work_dir / "eval")
        raws = backend.transcriber.transcribe(audio.path)
        windows = backend.match_windows(audio)
        segments = align_module.align_segments(raws, windows, meeting_id="m_eval")
        elapsed = time.perf_counter() - started
    finally:
        conn.close()

    names = {record_id: name for record_id, (name, _role) in backend.speaker_names.items()}
    payload = {
        "meeting": str(meeting_path),
        "annotation": str(annotation_path),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model": backend.transcriber.resolve_model(),
        "device": backend.transcriber.resolve_device(),
        "encoder": backend.encoder.metadata().get("name"),
        "window": settings.speaker_window,
        "hop": settings.speaker_hop,
        "topk": settings.speaker_topk,
        "min_score": settings.speaker_min_score,
        "min_margin": settings.speaker_min_margin,
        "speakers": len(backend.profiles),
        "audio_seconds": round(audio.duration, 3),
        "elapsed_seconds": round(elapsed, 3),
        "rtf": round(elapsed / audio.duration, 4) if audio.duration else 0.0,
        "metrics": {
            "window": evaluate_windows(windows, annotation, names, mapping),
            "segment": evaluate_segments(segments, annotation, names, mapping),
            "text": text_metrics(segments, annotation),
        },
        "segment_count": len(segments),
        "window_count": len(windows),
    }

    metrics_path = Path(args.metrics)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(Path(args.report), payload, segments, names, mapping)

    window_metrics = payload["metrics"]["window"]
    print("窗口级准确率：%.4f（%d 个窗口）" % (window_metrics["window_accuracy"], window_metrics["windows_scored"]))
    print("宏平均召回率：%.4f" % window_metrics["macro_recall"])
    print("片段级准确率：%.4f" % payload["metrics"]["segment"]["segment_accuracy"])
    print("RTF：%.4f（%.1f 秒 / %.1f 秒）" % (payload["rtf"], elapsed, audio.duration))
    print("报告：%s" % args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())