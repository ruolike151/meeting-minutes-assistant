"""端到端测试：上传音频 -> 生成纪要 -> 追问 -> 带引用回答 -> 导出。

全程使用离线感知后端与规则编排，不需要 GPU、模型 Key 与网络（约束 C3）。
"""

from __future__ import annotations

import json
import re


def parse_sse(text: str) -> list[tuple[str, dict]]:
    return [
        (match.group(1), json.loads(match.group(2)))
        for match in re.finditer(r"event: (\w+)\ndata: (.*)", text)
    ]


def upload(client, filename: str) -> str:
    upload_response = client.post(
        "/api/audio", files={"file": (filename, b"fake-audio-bytes" * 8, "audio/mpeg")}
    )
    assert upload_response.status_code == 200
    task_id = upload_response.json()["task_id"]
    events = parse_sse(client.get("/api/tasks/" + task_id + "/events").text)
    return dict(events)["result"]["meeting_id"]


def test_upload_ask_and_export_loop(client) -> None:
    meeting_id = upload(client, "支付双周会.mp3")

    detail = client.get("/api/meetings/" + meeting_id).json()
    assert detail["title"] == "支付双周会"
    assert len(detail["segments"]) == 6
    assert len(detail["action_items"]) == 3
    assert len(detail["review_items"]) == 2

    # 1. 内容追问：必须带引用，且引用指向这场新会议的片段
    reply = client.post(
        "/api/chat?stream=0",
        json={"message": "退款到账为什么这么慢？", "meeting_id": meeting_id},
    ).json()
    assert reply["answer"]["answer_mode"] == "content"
    assert reply["answer"]["citations"]
    assert {item["meeting_id"] for item in reply["answer"]["citations"]} == {meeting_id}

    # 2. 结构化追问：风险来自纪要，范围锁定在新会议
    risk = client.post(
        "/api/chat?stream=0",
        json={"message": "这场会有什么风险？", "meeting_id": meeting_id},
    ).json()
    assert risk["answer"]["answer_mode"] == "risks"
    assert risk["answer"]["scope_meeting_ids"] == [meeting_id]

    segment_ids = {segment["id"] for segment in detail["segments"]}
    assert all(item["segment_id"] in segment_ids for item in risk["answer"]["citations"])

    # 3. 导出：Markdown 与对话记录都能落地
    markdown = client.get("/api/export/" + meeting_id, params={"format": "md"}).text
    assert "退款到账时长问题" in markdown
    assert "资金链路" in markdown

    conversation_md = client.get(
        "/api/conversations/" + reply["conversation_id"] + "/export", params={"format": "md"}
    ).text
    assert "引用片段" in conversation_md
    assert "退款" in conversation_md


def test_human_review_flow_updates_exports(client) -> None:
    """人在回路：确认待确认条目后，条目并入最终稿并反映到导出。"""
    review_items = client.get("/api/review").json()
    target = next(item for item in review_items if item["meeting_id"] == "m_20260904")

    resolved = client.post(
        "/api/review/" + target["id"] + "/resolve",
        json={"decision": "confirm", "owner": "p_wang"},
    )
    assert resolved.status_code == 200

    detail = client.get("/api/meetings/m_20260904").json()
    item = next(entry for entry in detail["action_items"] if entry["id"] == target["id"].split(":")[1])
    assert item["needs_review"] is False
    if target["id"] == "m_20260904:a3":
        assert item["owner"] == "p_wang"

    markdown = client.get("/api/export/m_20260904", params={"format": "md"}).text
    assert "C" in markdown


def test_upload_review_resume_flow(client) -> None:
    """M4 完整闭环：上传 → 挂起等确认 → 逐条确认 → 收尾 → 继续追问。

    这条用例是"人在回路"的验收线：挂起不是失败，确认之后会议才算处理完，
    处理完之后追问仍然带引用。
    """
    meeting_id = upload(client, "移动端体验专项讨论.mp3")

    detail = client.get("/api/meetings/" + meeting_id).json()
    assert detail["status"] == "awaiting_review"
    review_items = [item for item in detail["review_items"] if item["meeting_id"] == meeting_id]
    assert review_items, "离线样例纪要里的低置信度条目必须进待确认区"

    # 逐条确认；最后一条确认后会议应转为 processed
    last = None
    for index, item in enumerate(review_items, start=1):
        last = client.post(
            "/api/review/" + item["id"] + "/resolve", json={"decision": "confirm"}
        )
        assert last.status_code == 200
        assert last.json()["remaining"] == len(review_items) - index
    assert last is not None
    assert last.json()["finalized"] is True

    detail = client.get("/api/meetings/" + meeting_id).json()
    assert detail["status"] == "processed"
    assert detail["review_items"] == []

    # 收尾之后追问照旧：带引用，且引用属于这场会议
    reply = client.post(
        "/api/chat?stream=0",
        json={"message": "这场会确定了什么？", "meeting_id": meeting_id},
    ).json()
    assert reply["answer"]["citations"]
    assert {item["meeting_id"] for item in reply["answer"]["citations"]} == {meeting_id}


def test_task_events_emit_review_event(client) -> None:
    """挂着人工确认的任务要在 SSE 里说明原因，前端据此切到待确认区。"""
    upload_response = client.post(
        "/api/audio", files={"file": ("发票流程复盘.mp3", b"fake" * 16, "audio/mpeg")}
    )
    task_id = upload_response.json()["task_id"]
    events = parse_sse(client.get("/api/tasks/" + task_id + "/events").text)
    names = [name for name, _ in events]
    assert "review" in names
    payload = dict(events)["review"]
    assert payload["count"] >= 1
    assert payload["items"][0]["kind"] in ("action_item", "decision")
