"""原声端点（M4 T6）：证据回跳要能听到那句话，也要防目录穿越。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.domain.models import Meeting
from app.store.repos import MeetingRepo

MEETING_ID = "m_audio_test"
AUDIO_BYTES = bytes(range(256)) * 8  # 2 KB 的假音频，够测 Range


def seed_meeting_with_audio(conn, settings, *, audio_file: str | None) -> None:
    if audio_file == "meeting.mp3":
        (settings.audio_dir / "meeting.mp3").write_bytes(AUDIO_BYTES)
    MeetingRepo(conn).create(
        Meeting(
            id=MEETING_ID,
            title="原声回跳测试",
            date=date(2026, 9, 12),
            duration=10,
            status="processed",
            audio_file=audio_file,
        )
    )


def test_audio_endpoint_serves_file_with_range_support(client, conn, settings) -> None:
    seed_meeting_with_audio(conn, settings, audio_file="meeting.mp3")

    response = client.get("/api/meetings/%s/audio" % MEETING_ID)
    assert response.status_code == 200
    assert response.content == AUDIO_BYTES
    assert response.headers.get("accept-ranges") == "bytes"

    partial = client.get(
        "/api/meetings/%s/audio" % MEETING_ID, headers={"Range": "bytes=0-1023"}
    )
    assert partial.status_code == 206
    assert len(partial.content) == 1024
    assert "content-range" in {key.lower() for key in partial.headers}


def test_audio_endpoint_404_when_meeting_has_no_audio(client, conn, settings) -> None:
    seed_meeting_with_audio(conn, settings, audio_file=None)
    response = client.get("/api/meetings/%s/audio" % MEETING_ID)
    assert response.status_code == 404


def test_audio_endpoint_404_when_file_is_missing(client, conn, settings) -> None:
    seed_meeting_with_audio(conn, settings, audio_file="gone.mp3")
    response = client.get("/api/meetings/%s/audio" % MEETING_ID)
    assert response.status_code == 404


def test_audio_endpoint_rejects_path_traversal(client, conn, settings) -> None:
    """`audio_file` 里塞 `../` 不能读到音频目录之外。"""
    secret = settings.data_dir / "secret.mp3"
    secret.write_bytes(b"top-secret")
    seed_meeting_with_audio(conn, settings, audio_file="../secret.mp3")
    response = client.get("/api/meetings/%s/audio" % MEETING_ID)
    assert response.status_code == 400


def test_audio_endpoint_404_for_unknown_meeting(client) -> None:
    response = client.get("/api/meetings/m_nope/audio")
    assert response.status_code == 404


def test_detail_reports_audio_availability(client, conn, settings) -> None:
    """会议详情要告诉前端"原声到底能不能播"。

    用户反馈过"右侧原声无法播放"：样例会议只有转写、没有音频文件，
    前端却渲染了播放器。现在按实际文件是否存在给出 audio_available。
    """
    seed_meeting_with_audio(conn, settings, audio_file="meeting.mp3")
    assert client.get("/api/meetings/%s" % MEETING_ID).json()["audio_available"] is True

    # 种子样例会议（原型数据）没有音频文件 → False，前端显示说明文案
    assert (
        client.get("/api/meetings/m_20260911").json()["audio_available"] is False
    )


def test_detail_marks_missing_audio_as_unavailable(client, conn, settings) -> None:
    seed_meeting_with_audio(conn, settings, audio_file=None)
    detail = client.get("/api/meetings/%s" % MEETING_ID).json()
    assert detail["audio_available"] is False


def test_import_samples_exposes_playable_audio(conn, settings) -> None:
    """导入真实录音后：会议在原声目录里有文件，端点可播（206 支持 Range）。"""
    from app.eval.dataset import load_dataset
    from app.eval.fixtures import ingest_real_meetings

    root = Path(__file__).resolve().parents[1]
    dataset = load_dataset(root / "samples" / "annotations" / "m5")
    ingest_real_meetings(conn, dataset, audio_target_dir=settings.audio_dir)

    for case in dataset.meetings:
        assert (settings.audio_dir / case.audio_file).is_file()
