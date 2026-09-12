#!/usr/bin/env python
"""把 M2 需要的模型权重下载到 `model/` 目录（不依赖参考项目的任何路径）。

    转写：Systran/faster-whisper-small、Systran/faster-whisper-medium
    声纹：speechbrain/spkrec-ecapa-voxceleb

huggingface.co 在部分网络下不可达，脚本默认走 hf-mirror.com 镜像，
可用环境变量 HF_ENDPOINT 覆盖。已存在的目录默认跳过，加 --force 重新下载。

用法：

    .\meeting_assistant\Scripts\python.exe scripts\download_models.py
    .\meeting_assistant\Scripts\python.exe scripts\download_models.py --sizes small
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_ENDPOINT = "https://hf-mirror.com"

TRANSCRIPTION_REPOS = {
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
}
SPEAKER_REPO = "speechbrain/spkrec-ecapa-voxceleb"
SPEAKER_DIRNAME = "speechbrain-spkrec-ecapa-voxceleb"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载 M2 感知层模型权重到 model/")
    parser.add_argument("--model-dir", default=str(ROOT / "model"), help="权重目录")
    parser.add_argument(
        "--sizes",
        nargs="+",
        default=["small", "medium"],
        choices=sorted(TRANSCRIPTION_REPOS),
        help="要下载的转写模型规格",
    )
    parser.add_argument("--skip-speaker", action="store_true", help="跳过声纹模型")
    parser.add_argument("--force", action="store_true", help="已存在也重新下载")
    return parser.parse_args()


def download(
    repo_id: str,
    target: Path,
    *,
    force: bool,
    required: tuple[str, ...] = ("model.bin",),
) -> Path:
    """下载一个仓库到 target。

    `required` 是关键权重文件名：只有它存在且不是一个空壳，才算已下载完成。
    之前只判断「目录非空」会把上一次中断留下的残缺目录当成完整副本，
    结果加载模型时才发现少了几个 G 的权重。
    """
    # hf-mirror 不支持 Xet 协议（会 401），必须回退到普通 HTTP 下载
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    from huggingface_hub import snapshot_download

    if not force and target.is_dir():
        ready = all(
            (target / name).is_file() and (target / name).stat().st_size > 1024 * 1024
            for name in required
        )
        if ready:
            print("[skip] %s 已完整：%s" % (repo_id, target))
            return target
        print("[redo] %s 之前没下完，重新下载：%s" % (repo_id, target))
    target.mkdir(parents=True, exist_ok=True)
    print("[get ] %s -> %s" % (repo_id, target))
    # huggingface_hub 1.x 去掉了 local_dir_use_symlinks / resume_download，
    # 这里只传最小参数集，兼容 0.2x 与 1.x。
    snapshot_download(repo_id=repo_id, local_dir=str(target))
    return target


def main() -> int:
    args = parse_args()
    os.environ.setdefault("HF_ENDPOINT", DEFAULT_ENDPOINT)
    model_dir = Path(args.model_dir).expanduser().absolute()
    model_dir.mkdir(parents=True, exist_ok=True)
    print("镜像：%s" % os.environ["HF_ENDPOINT"])

    for size in args.sizes:
        download(
            TRANSCRIPTION_REPOS[size],
            model_dir / ("faster-whisper-%s" % size),
            force=args.force,
        )

    if not args.skip_speaker:
        download(SPEAKER_REPO, model_dir / SPEAKER_DIRNAME, force=args.force)

    print("完成。模型目录：%s" % model_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())