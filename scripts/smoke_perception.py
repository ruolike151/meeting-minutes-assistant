#!/usr/bin/env python
"""真实感知链路冒烟（不进 CI，也不在 pytest 里跑）。

跑一遍完整链路并把结果写进数据目录：

    音频标准化 -> faster-whisper 转写 -> ECAPA 滑窗声纹匹配 -> 片段身份对齐 -> 入库

    .\meeting_assistant\Scripts\python.exe scripts\smoke_perception.py `
      --meeting samples\audio\meeting.mp3 --register-samples

输出片段表、参会人、端到端耗时与 RTF（实时率 = 处理耗时 / 音频时长）。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings                        # noqa: E402
from app.perception.local import LocalPerception       # noqa: E402
from app.store.db import connect, migrate              # noqa: E402
from app.store.repos import MeetingRepo                # noqa: E402
from scripts.enroll_samples import enroll_from_manifest  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="跑一遍真实感知链路")
    parser.add_argument("--meeting", default=str(ROOT / "samples" / "audio" / "meeting.mp3"))
    parser.add_argument("--data-dir", default=None, help="数据目录（默认 data/）")
    parser.add_argument("--title", default=None)
    parser.add_argument(
        "--register-samples",
        action="store_true",
        help="先把 samples 里的 4 个注册音频写进声纹库",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = Settings.from_env(args.data_dir)
    settings.ensure_dirs()

    audio_path = Path(args.meeting)
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        if args.register_samples:
            enroll_from_manifest(settings, conn)

        backend = LocalPerception.from_settings(settings, conn)
        print("转写设备：%s / 声纹编码器：%s" % (
            backend.transcriber.resolve_device(),
            backend.encoder.metadata().get("name"),
        ))
        print("声纹库人数：%d" % len(backend.profiles))

        def progress(index: int, label: str) -> None:
            print("  [%d] %s" % (index + 1, label), flush=True)

        started = time.perf_counter()
        meeting, segments = backend.process(
            audio_path,
            meeting_id="m_smoke",
            title=args.title or audio_path.stem,
            progress=progress,
        )
        elapsed = time.perf_counter() - started

        MeetingRepo(conn).create(meeting, segments, None)
    finally:
        conn.close()

    names = {p.id: p.name for p in meeting.participants}
    print("")
    print("片段（%d 条）：" % len(segments))
    for segment in segments:
        print(
            "  %7.2f-%7.2f  %-8s %.2f  %s"
            % (
                segment.start,
                segment.end,
                names.get(segment.speaker_id or "", "未识别"),
                segment.speaker_confidence,
                segment.text[:40],
            )
        )
    duration = meeting.duration or 0.0
    print("")
    print("音频 %.1f 秒，处理 %.1f 秒，RTF = %.3f" % (duration, elapsed, elapsed / duration if duration else 0.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())