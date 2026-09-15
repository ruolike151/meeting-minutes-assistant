"""HTTP 层测试：状态码、响应 schema、SSE 事件契约与静态资源托管。"""

from __future__ import annotations

import json
import re


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for match in re.finditer(r"event: (\w+)\ndata: (.*)", text):
        events.append((match.group(1), json.loads(match.group(2))))
    return events


def test_meeting_library(client) -> None:
    body = client.get("/api/meetings").json()
    assert [item["id"] for item in body["meetings"]] == [
        "m_20260911",
        "m_20260909",
        "m_20260904",
    ]
    first = body["meetings"][0]
    assert first["segment_count"] == 12
    assert first["review_count"] == 1
    assert first["participants"][0]["name"] == "A"


def test_meeting_library_filters(client) -> None:
    body = client.get("/api/meetings", params={"q": "会员"}).json()
    assert [item["id"] for item in body["meetings"]] == ["m_20260909"]
    body = client.get("/api/meetings", params={"participant": "E"}).json()
    assert {item["id"] for item in body["meetings"]} == {"m_20260911", "m_20260909"}


def test_meeting_detail_aggregates_view(client) -> None:
    detail = client.get("/api/meetings/m_20260911").json()
    assert detail["title"] == "官网改版周会"
    assert len(detail["segments"]) == 12
    assert len(detail["action_items"]) == 4
    assert len(detail["decisions"]) == 3
    assert [item["id"] for item in detail["review_items"]] == ["m_20260911:a4"]
    assert detail["review_items"][0]["owner_name"] == "A"
    assert detail["review_items"][0]["citations"][0]["segment_id"] in {"s7", "s9"}


def test_meeting_detail_404(client) -> None:
    assert client.get("/api/meetings/m_none").status_code == 404


def test_minutes_endpoint(client) -> None:
    minutes = client.get("/api/meetings/m_20260909/minutes").json()
    assert minutes["title"] == "会员体系二期需求评审"
    assert len(minutes["action_items"]) == 2
    assert client.get("/api/meetings/m_none/minutes").status_code == 404


def test_patch_action_item_reflects_in_detail_and_export(client) -> None:
    response = client.patch("/api/action-items/m_20260911:a1", json={"status": "done"})
    assert response.status_code == 200
    assert response.json()["item"]["status"] == "done"

    detail = client.get("/api/meetings/m_20260911").json()
    item = next(entry for entry in detail["action_items"] if entry["id"] == "a1")
    assert item["status"] == "done"

    csv_text = client.get("/api/export/m_20260911", params={"format": "csv"}).text
    assert "已完成" in csv_text


def test_patch_action_item_rejects_bad_id(client) -> None:
    assert client.patch("/api/action-items/a1", json={"status": "done"}).status_code == 400
    assert (
        client.patch("/api/action-items/m_20260911:a99", json={"status": "done"}).status_code
        == 404
    )


def test_review_list_and_confirm(client) -> None:
    items = client.get("/api/review").json()
    assert {item["id"] for item in items} == {
        "m_20260911:a4",
        "m_20260909:a2",
        "m_20260904:a2",
        "m_20260904:a3",
    }
    scoped = client.get("/api/review", params={"meeting_id": "m_20260904"}).json()
    assert len(scoped) == 2

    resolved = client.post("/api/review/m_20260911:a4/resolve", json={"decision": "confirm"})
    assert resolved.status_code == 200
    assert resolved.json()["decision"] == "confirm"

    remaining = {item["id"] for item in client.get("/api/review").json()}
    assert "m_20260911:a4" not in remaining
    detail = client.get("/api/meetings/m_20260911").json()
    item = next(entry for entry in detail["action_items"] if entry["id"] == "a4")
    assert item["needs_review"] is False


def test_review_dismiss_and_unknown_item(client) -> None:
    assert client.post(
        "/api/review/m_20260904:a3/resolve", json={"decision": "dismiss"}
    ).status_code == 200
    detail = client.get("/api/meetings/m_20260904").json()
    assert all(item["id"] != "a3" for item in detail["action_items"])
    assert client.post(
        "/api/review/m_20260904:a99/resolve", json={"decision": "confirm"}
    ).status_code == 404


def test_export_formats(client) -> None:
    markdown = client.get("/api/export/m_20260911", params={"format": "md"})
    assert markdown.status_code == 200
    assert markdown.text.startswith("# 官网改版周会")
    assert "filename*=UTF-8''" in markdown.headers["content-disposition"]
    assert "待办" in markdown.text

    csv_response = client.get("/api/export/m_20260911", params={"format": "csv"})
    assert csv_response.text.startswith("\ufeff")
    assert "是否待确认" in csv_response.text

    payload = client.get("/api/export/m_20260911", params={"format": "json"}).json()
    assert payload["meeting"]["id"] == "m_20260911"
    assert payload["minutes"]["meeting_id"] == "m_20260911"

    assert client.get("/api/export/m_20260911", params={"format": "pdf"}).status_code == 422
    assert client.get("/api/export/m_none").status_code == 404


def test_chat_json_and_conversation_history(client) -> None:
    response = client.post(
        "/api/chat",
        params={"stream": "0"},
        json={"message": "官网改版周会定了什么？", "meeting_id": "m_20260911"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"]["answer_mode"] == "decisions"
    assert body["answer"]["scope_meeting_ids"] == ["m_20260911"]
    assert body["message"]["role"] == "assistant"
    assert body["message"]["evidence"]

    conversation = client.get("/api/conversations/" + body["conversation_id"])
    assert conversation.status_code == 200
    assert [message["role"] for message in conversation.json()["messages"]] == [
        "user",
        "assistant",
    ]
    assert client.get("/api/conversations/c_none").status_code == 404


def test_chat_stream_emits_documented_events(client) -> None:
    response = client.post(
        "/api/chat?stream=1",
        json={"message": "谁提到了活动弹窗的问题？"},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    events = parse_sse(response.text)
    names = [name for name, _ in events]
    assert names[:3] == ["stage", "stage", "stage"]
    assert names[-1] == "done"
    assert "citation" in names and "delta" in names

    done = dict(events)["done"]
    assert done["answer"]["answer_mode"] == "attribution"
    assert done["conversation_id"].startswith("c_")
    citation = next(data for name, data in events if name == "citation")
    assert citation["meeting_id"] == "m_20260911"


def test_refusal_is_explicit(client) -> None:
    response = client.post(
        "/api/chat?stream=0", json={"message": "公司食堂今天中午吃什么？"}
    )
    answer = response.json()["answer"]
    assert answer["answer_mode"] == "refuse"
    assert answer["citations"] == []


def test_audio_upload_runs_offline_pipeline(client) -> None:
    upload = client.post(
        "/api/audio",
        files={"file": ("双周进度对齐.mp3", b"fake-audio-bytes", "audio/mpeg")},
    )
    assert upload.status_code == 200
    task = upload.json()
    assert len(task["stages"]) == 4

    events = parse_sse(client.get("/api/tasks/" + task["task_id"] + "/events").text)
    names = [name for name, _ in events]
    assert names.count("stage") == 4
    assert names[-2:] == ["result", "done"]
    meeting_id = dict(events)["result"]["meeting_id"]

    status = client.get("/api/tasks/" + task["task_id"]).json()
    assert status["status"] == "done"
    assert status["progress"] == 1.0

    library = client.get("/api/meetings").json()["meetings"]
    assert meeting_id in {item["id"] for item in library}
    detail = client.get("/api/meetings/" + meeting_id).json()
    assert detail["title"] == "双周进度对齐"
    assert len(detail["segments"]) == 6


def test_audio_upload_rejects_empty_file(client) -> None:
    response = client.post("/api/audio", files={"file": ("empty.mp3", b"", "audio/mpeg")})
    assert response.status_code == 400


def test_task_events_unknown_task(client) -> None:
    events = parse_sse(client.get("/api/tasks/t_none/events").text)
    assert events[0][0] == "error"


def test_static_frontend_is_served(client) -> None:
    index = client.get("/")
    assert index.status_code == 200
    assert "app.js" in index.text
    assert client.get("/styles.css").status_code == 200
    assert client.get("/app.js").status_code == 200
    assert client.get("/api/meetings").headers["content-type"].startswith("application/json")


def test_openapi_lists_m1_routes(client) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for route in (
        "/api/meetings",
        "/api/meetings/{meeting_id}",
        "/api/meetings/{meeting_id}/minutes",
        "/api/action-items/{item_id}",
        "/api/review",
        "/api/review/{item_id}/resolve",
        "/api/chat",
        "/api/conversations/{conversation_id}",
        "/api/audio",
        "/api/tasks/{task_id}/events",
        "/api/export/{meeting_id}",
    ):
        assert route in paths


def test_export_ascii_fallback_is_usable(client) -> None:
    """纯中文标题不能把 ASCII 回退名退化成 `_.md`（RFC 6266 回退分支）。"""
    header = client.get("/api/export/m_20260911", params={"format": "md"}).headers[
        "content-disposition"
    ]
    assert 'filename="export.md"' in header
    assert "filename*=UTF-8''" in header


def test_conversation_list_supports_switching(client) -> None:
    """侧栏的"对话"栏靠这个接口切换历史会话。"""
    assert client.get("/api/conversations").json() == []

    first = client.post(
        "/api/chat?stream=0", json={"message": "官网改版周会定了什么？"}
    ).json()
    client.post(
        "/api/chat?stream=0",
        json={"message": "那还有哪些待办？", "conversation_id": first["conversation_id"]},
    )

    items = client.get("/api/conversations").json()
    assert len(items) == 1
    item = items[0]
    assert item["id"] == first["conversation_id"]
    # 标题取第一条用户消息，消息数是两轮问答（4 条）
    assert item["title"] == "官网改版周会定了什么？"
    assert item["message_count"] == 4
    assert item["updated_at"] >= item["created_at"]

    # limit 生效，且越界值被 422 拦下（不给前端塞一个拖垮库的查询）
    assert len(client.get("/api/conversations", params={"limit": 1}).json()) == 1
    assert client.get("/api/conversations", params={"limit": 0}).status_code == 422


def test_static_assets_revalidate_every_time(client) -> None:
    """静态资源带 no-cache：改了前端刷新就能生效，不被启发式缓存挡住。

    踩过的坑：StaticFiles 默认不发缓存头，浏览器直接吃缓存，
    "服务端已经是新文件、页面还是旧行为"，排查了半天。
    """
    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-cache"
    script = client.get("/chat.js")
    assert script.status_code == 200
    assert script.headers["cache-control"] == "no-cache"
    # API 响应不受影响（数据本来就该由业务决定缓存策略）
    assert "cache-control" not in client.get("/api/meetings").headers
