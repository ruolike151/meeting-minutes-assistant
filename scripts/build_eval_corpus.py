#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把真实会议语料合成为评测用的 `samples/annotations/m5/real_meetings.json`（M5）。

为什么要有这一步：转写稿（`samples/annotations/meeting*.demo.json`）是**上游产出的原始
标注**，评测语料是**带金标的会议对象**，两者格式不同。手抄 31 段正文既容易出错，
也说不清"这段文本从哪来"。因此：

* 正文、时间戳、说话人标签 → 从转写稿**机械转换**（可重复执行、结果一致）；
* 纪要金标（决议 / 待办 / 风险）→ 人工撰写，放在 `real_minutes_gold.json`；
* 两者在这里合成，输出带 `source: real_meeting` 与来源说明的语料文件。

```powershell
.\\meeting_assistant\\Scripts\\python.exe scripts\\build_eval_corpus.py
```

约定（与《M5-评测阶段实施计划》第 5.1 节一致）：

* `GROUP`（多人合说）在领域模型里没有承载字段，转换为 `speaker_id=null` +
  置信度 0.5，评测时照常使用其文本（感知层的说话人指标另有口径）；
* 片段 ID 统一为 `s1..sN`，供检索 / 问答 / 纪要金标引用。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ANNOTATION_DIR = ROOT / "samples" / "annotations"
M5_DIR = ANNOTATION_DIR / "m5"
GOLD_FILE = M5_DIR / "real_minutes_gold.json"
OUTPUT_FILE = M5_DIR / "real_meetings.json"

GROUP_SPEAKER = "GROUP"
GROUP_CONFIDENCE = 0.5


def load_gold() -> dict:
    return json.loads(GOLD_FILE.read_text(encoding="utf-8"))


def convert_meeting(gold_case: dict, *, version: str, updated_at: str) -> dict:
    transcript = json.loads(
        (ANNOTATION_DIR / gold_case["annotation_file"]).read_text(encoding="utf-8")
    )
    participants = []
    for label, info in sorted(gold_case["participants"].items()):
        participants.append(
            {"id": info["id"], "name": info["name"], "role": info.get("role"), "label": label}
        )
    label_to_id = {label: info["id"] for label, info in gold_case["participants"].items()}

    segments = []
    for index, raw in enumerate(transcript["segments"], start=1):
        label = raw["speaker"]
        is_group = label == GROUP_SPEAKER
        segments.append(
            {
                "id": "s%d" % index,
                "start": float(raw["start"]),
                "end": float(raw["end"]),
                "speaker_id": None if is_group else label_to_id.get(label),
                "speaker_confidence": GROUP_CONFIDENCE
                if is_group
                else float(raw.get("speaker_confidence", 1.0)),
                "text": raw["text"],
                "note": "多人合说：领域模型不承载，按未识别处理" if is_group else "",
            }
        )

    action_items = []
    for item in gold_case["minutes"].get("action_items", []):
        action_items.append(
            {
                "task": item["task"],
                "owner": label_to_id.get(item["owner"]) if item.get("owner") else None,
                "due": item.get("due"),
                "evidence": item.get("evidence", []),
            }
        )
    return {
        "meeting_id": gold_case["meeting_id"],
        "title": gold_case["title"],
        "date": gold_case["date"],
        "time": gold_case.get("time"),
        "duration": gold_case["duration"],
        "audio_file": gold_case["audio_file"],
        "participants": participants,
        "segments": segments,
        "minutes": {
            "decisions": gold_case["minutes"].get("decisions", []),
            "action_items": action_items,
            "risks": gold_case["minutes"].get("risks", []),
        },
    }


def build(output: Path = OUTPUT_FILE) -> dict:
    gold = load_gold()
    meetings = [
        convert_meeting(case, version=gold["version"], updated_at=gold["updated_at"])
        for case in gold["cases"]
    ]
    payload = {
        "version": gold["version"],
        "updated_at": gold["updated_at"],
        "source": gold.get("source", "real_meeting"),
        "notes": gold.get("notes", []),
        "cases": meetings,
    }
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="合成真实会议评测语料")
    parser.add_argument("--output", default=str(OUTPUT_FILE))
    args = parser.parse_args()
    payload = build(Path(args.output))
    print(
        "已生成 %s：%d 场会议 / %d 条片段"
        % (
            args.output,
            len(payload["cases"]),
            sum(len(case["segments"]) for case in payload["cases"]),
        )
    )
    for case in payload["cases"]:
        print(
            "  · %s %s：%d 片段 / %d 待办 / %d 决议 / %d 风险"
            % (
                case["meeting_id"],
                case["title"],
                len(case["segments"]),
                len(case["minutes"]["action_items"]),
                len(case["minutes"]["decisions"]),
                len(case["minutes"]["risks"]),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
