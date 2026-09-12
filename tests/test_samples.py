"""样例素材完整性（M2 T0）。

素材是可选的：新克隆的仓库、或没拿到录音的机器上，本文件整体跳过，
不会因为缺文件把测试基线弄红（约束 C3 / C6）。
标注集同时是 M5 评测的基准，因此这里校验的是「它能不能当基准用」。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
ANNOTATIONS = SAMPLES / "annotations"
AUDIO = SAMPLES / "audio"

MEETING_AUDIO = ["meeting.mp3", "meeting2.mp3"]
SOLO_AUDIO = ["A_solo.mp3", "B_solo.mp3", "C_solo.mp3", "D_solo.mp3"]

pytestmark = pytest.mark.skipif(not ANNOTATIONS.is_dir(), reason="样例素材未随仓库提供")


MEETING_ANNOTATIONS = ["meeting.demo.json", "meeting2.demo.json"]
SPEAKER_MANIFEST = "speakers.json"


def test_expected_annotations_are_present() -> None:
    names = sorted(path.name for path in ANNOTATIONS.glob("*.json"))
    assert names == sorted(MEETING_ANNOTATIONS + [SPEAKER_MANIFEST])


def test_speaker_manifest_maps_labels_to_names_and_audio() -> None:
    """A-D 标签必须能对上角色名与注册音频（评测脚本靠它做身份对齐）。"""
    payload = json.loads((ANNOTATIONS / SPEAKER_MANIFEST).read_text(encoding="utf-8"))
    speakers = payload["speakers"]
    assert [item["label"] for item in speakers] == ["A", "B", "C", "D"]
    for item in speakers:
        assert item["name"].strip()
        audio = AUDIO / item["audio"]
        if AUDIO.is_dir():
            assert audio.is_file(), "缺少注册音频 %s" % item["audio"]


def test_annotations_are_wellformed() -> None:
    files = [ANNOTATIONS / name for name in MEETING_ANNOTATIONS]
    assert files
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        participants = payload.get("participants")
        assert isinstance(participants, dict) and participants, path.name
        segments = payload["segments"]
        assert segments, path.name
        previous_end = -1.0
        for segment in segments:
            assert segment["speaker"] in participants, "%s: %s" % (path.name, segment)
            assert str(segment["text"]).strip(), path.name
            start = float(segment["start"])
            end = float(segment["end"])
            assert 0.0 <= start <= end, path.name
            assert start >= previous_end - 1e-6, "%s: 标注片段必须按时间递增" % path.name
            previous_end = end


@pytest.mark.skipif(not AUDIO.is_dir(), reason="样例音频未随仓库提供")
def test_sample_audio_is_present() -> None:
    for name in MEETING_AUDIO + SOLO_AUDIO:
        target = AUDIO / name
        assert target.is_file(), "缺少样例音频 %s" % name
        assert target.stat().st_size > 1000, "%s 看起来是空文件" % name