"""感知层契约测试（《技术方案》14.1）。

「新增一个后端时，同一组断言自动覆盖它」——这正是 M2 把 offline 与 local
放在同一个协议下的意义：接入真实模型不会让测试基线失效。

新增后端时把它加进 `BACKENDS` 即可。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.perception.base import PerceptionBackend
from app.perception.offline import OfflinePerception


def assert_backend_contract(backend: PerceptionBackend, tmp_path: Path) -> None:
    stages = backend.stages()
    assert stages, "后端必须声明处理阶段"
    assert all(isinstance(item, str) and item for item in stages)

    audio = tmp_path / "whatever.mp3"      # 离线后端不读文件内容，这里只给路径

    raws = backend.transcribe(audio)
    assert raws, "转写结果不能为空"
    previous_end = -1.0
    for raw in raws:
        assert raw.end >= raw.start
        assert raw.text.strip()
        assert 0.0 <= raw.confidence <= 1.0
        assert raw.start >= previous_end - 1e-9, "片段必须按时间递增且不重叠"
        previous_end = raw.end

    turns = backend.identify_speakers(audio, raws)
    assert len(turns) == len(raws)

    meeting, segments = backend.process(audio, meeting_id="m_test", title="契约测试")
    assert meeting.id == "m_test"
    assert meeting.title == "契约测试"
    assert segments

    participant_ids = {p.id for p in meeting.participants}
    if any(segment.speaker_id for segment in segments):
        assert participant_ids, "有说话人的片段必须有对应参会人"
    for segment in segments:
        assert segment.meeting_id == "m_test"
        assert 0.0 <= segment.speaker_confidence <= 1.0
        if segment.speaker_id is not None:
            assert segment.speaker_id in participant_ids, "落库的说话人必须在参会人里"

    assert meeting.duration >= segments[-1].end - 1e-6
    # 片段 ID 唯一：证据链（约束 C4）靠它被引用
    assert len({segment.id for segment in segments}) == len(segments)


def test_offline_backend_satisfies_contract(tmp_path: Path) -> None:
    assert_backend_contract(OfflinePerception(), tmp_path)


def test_offline_backend_ignores_audio_content(tmp_path: Path) -> None:
    """离线后端不读音频内容——这是它能在没有 ffmpeg 的机器上跑通的原因。"""
    backend = OfflinePerception()
    missing = tmp_path / "not-exists.mp3"
    meeting, segments = backend.process(missing, meeting_id="m_x", title="不存在的音频")
    assert meeting.audio_file == "not-exists.mp3"
    assert len(segments) == 6


def test_offline_placeholder_minutes_are_consistent(tmp_path: Path) -> None:
    """占位纪要引用的片段 ID 必须真实存在，否则证据链是坏的。"""
    backend = OfflinePerception()
    _, segments = backend.process(tmp_path / "a.mp3", meeting_id="m_y", title="占位纪要")
    known = {segment.id for segment in segments}
    minutes = backend.placeholder_minutes("m_y")
    assert minutes is not None
    referenced = {
        segment_id
        for topic in minutes.summary
        for segment_id in topic.evidence
    } | {segment_id for item in minutes.action_items for segment_id in item.evidence}
    assert referenced
    assert referenced <= known