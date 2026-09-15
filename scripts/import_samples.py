#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把两场真实录音导入会议库，让「原声」页签真的能播放。

```powershell
.\\meeting_assistant\\Scripts\\python.exe scripts\\import_samples.py
```

来源是仓库自带的素材：`samples/audio/meeting.mp3`（7 段转写）与 `meeting2.mp3`（24 段），
标注在 `samples/annotations/m5/real_meetings.json`（由 `scripts/build_eval_corpus.py` 合成）。
音频会复制进 `data/audio/`，因此证据回跳可以定位播放。

另外三场产品样例（`app/store/seeds.py`）是原型里的虚构会议，**只有转写没有音频**——
界面会给"没有可播放的原声文件"的说明，这是数据本身的边界，不是 bug。
重复执行是幂等的（按会议 ID 覆盖，人工确认结果存在 override 表，不受影响）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.eval.dataset import load_dataset  # noqa: E402
from app.eval.fixtures import SAMPLES_AUDIO_DIR, ingest_real_meetings  # noqa: E402
from app.store.db import open_db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="导入两场真实录音（带原声）")
    parser.add_argument("--dataset", default=None, help="标注集目录，默认 samples/annotations/m5")
    args = parser.parse_args()

    settings = Settings.from_env()
    dataset = load_dataset(args.dataset or settings.eval_dataset_dir)
    conn = open_db(settings)
    try:
        count = ingest_real_meetings(
            conn,
            dataset,
            audio_source_dir=SAMPLES_AUDIO_DIR,
            audio_target_dir=settings.audio_dir,
        )
        for case in dataset.meetings:
            audio = settings.audio_dir / (case.audio_file or "")
            print(
                "· %s 《%s》：%d 段转写，原声 %s"
                % (
                    case.meeting_id,
                    case.title,
                    len(case.segments),
                    "已就位" if audio.is_file() else "缺失",
                )
            )
        print("完成：导入 %d 场（库：%s）" % (count, settings.db_path))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
