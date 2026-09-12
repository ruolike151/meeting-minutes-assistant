"""声纹库仓储与注册工具（M2 T5）。

不依赖 numpy / torch：向量打包是纯 struct，仓储用合成向量，
工具层的音频编码被替换成假实现（真实编码在冒烟脚本里验证）。
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.store.speakers_repo import (
    SpeakerRepo,
    pack_vector,
    unpack_vector,
)
from app.tools.speakers import RegisterSpeakerInput, register_speaker


def test_pack_vector_roundtrip() -> None:
    blob = pack_vector([1.0, -0.5, 0.25])
    assert len(blob) == 12
    assert unpack_vector(blob, 3) == pytest.approx([1.0, -0.5, 0.25])


def test_unpack_vector_rejects_wrong_length() -> None:
    with pytest.raises(ValueError):
        unpack_vector(b"\x00\x00\x00\x00", 3)


def test_upsert_then_profiles(conn) -> None:
    repo = SpeakerRepo(conn)
    record = repo.upsert(name="项目经理", vectors=[[1.0, 0.0], [0.9, 0.1]], role="负责人")
    assert record.name == "项目经理"
    assert record.role == "负责人"
    assert record.dim == 2
    assert record.vector_count == 2

    profiles = repo.profiles(encoder="speechbrain_ecapa")
    assert set(profiles) == {record.id}
    assert len(profiles[record.id].vectors) == 2


def test_upsert_same_name_overwrites_instead_of_duplicating(conn) -> None:
    repo = SpeakerRepo(conn)
    first = repo.upsert(name="项目经理", vectors=[[1.0, 0.0]])
    second = repo.upsert(name="项目经理", vectors=[[0.0, 1.0], [0.1, 0.9]])
    assert first.id == second.id
    assert len(repo.all()) == 1
    assert second.vector_count == 2


def test_profiles_skip_vectors_from_other_encoder(conn) -> None:
    """换声纹模型后旧向量不能参与匹配，否则相似度没有意义。"""
    repo = SpeakerRepo(conn)
    repo.upsert(name="项目经理", vectors=[[1.0, 0.0]], encoder="speechbrain_ecapa")
    repo.upsert(name="运营专员", vectors=[[0.0, 1.0]], encoder="mfcc")
    assert len(repo.profiles(encoder="speechbrain_ecapa")) == 1
    assert len(repo.profiles()) == 2


def test_upsert_rejects_vectors_with_inconsistent_dimension(conn) -> None:
    repo = SpeakerRepo(conn)
    with pytest.raises(ValueError):
        repo.upsert(name="项目经理", vectors=[[1.0, 0.0], [1.0, 0.0, 0.0]])


def test_register_speaker_tool_writes_record(settings: Settings, conn, monkeypatch) -> None:
    class FakeEncoder:
        name = "fake_encoder"
        dim = 2

        def encode(self, samples, sample_rate):
            return [1.0, 0.0]

    monkeypatch.setattr("app.tools.speakers.create_encoder", lambda settings: FakeEncoder())
    monkeypatch.setattr(
        "app.perception.enroll.encode_speaker_audio",
        lambda settings, path, encoder=None: [[1.0, 0.0], [0.9, 0.1]],
    )

    output = register_speaker(
        settings,
        conn,
        RegisterSpeakerInput(name="项目经理", audio_path="samples/audio/A_solo.mp3", role="负责人"),
    )
    assert output.speaker_id.startswith("spk_")
    assert output.name == "项目经理"
    assert output.encoder == "fake_encoder"
    assert output.vector_count == 2

    profiles = SpeakerRepo(conn).profiles(encoder="fake_encoder")
    assert set(profiles) == {output.speaker_id}