"""导出接口与渲染函数（产品文档 5.5）。

四种产物：纪要 Markdown、待办 CSV、结构化 JSON、对话记录 Markdown。
渲染函数是纯函数，便于直接单测；接口只负责选格式、落盘、加响应头。
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.deps import get_context
from app.domain.models import Conversation, Meeting, Minutes
from app.store.files import write_export
from app.store.minutes_repo import MinutesRepo
from app.store.repos import MeetingRepo
from app.tools.registry import ToolContext

router = APIRouter(prefix="/api/export", tags=["export"])

FORMATS = {
    "md": ("text/markdown; charset=utf-8", "纪要.md"),
    "csv": ("text/csv; charset=utf-8", "待办.csv"),
    "json": ("application/json; charset=utf-8", "result.json"),
}


ASCII_SAFE = re.compile(r"[A-Za-z0-9]")


def content_disposition(filename: str) -> str:
    """中文文件名要同时给 ASCII 回退与 RFC 5987 编码（RFC 6266）。

    纯中文标题按 ASCII 编码后只剩 `_` 和扩展名，回退名会变成 `_.md`。
    没有可用的 ASCII 字符时给一个通用名，旧浏览器也能存下文件。
    """
    path = Path(filename)
    stem = path.stem.encode("ascii", "ignore").decode("ascii")
    suffix = path.suffix.encode("ascii", "ignore").decode("ascii") or ".txt"
    ascii_fallback = (stem if ASCII_SAFE.search(stem) else "export") + suffix
    return 'attachment; filename="%s"; filename*=UTF-8\'\'%s' % (
        ascii_fallback,
        quote(filename, safe=""),
    )


def mmss(seconds: float) -> str:
    total = int(seconds)
    return "%02d:%02d" % (total // 60, total % 60)


def duration_text(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes >= 60:
        return "%d 小时 %d 分" % (minutes // 60, minutes % 60)
    return "%d 分钟" % minutes


def owner_name(meeting: Meeting, owner: str | None) -> str:
    return meeting.speaker_name(owner) if owner else "未指定"


def due_text(item) -> str:
    return item.due_text or (item.due.isoformat() if item.due else "未定")


def minutes_markdown(meeting: Meeting, minutes: Minutes) -> str:
    lines: list[str] = [
        "# %s" % meeting.title,
        "",
        "- 日期：%s %s" % (meeting.date.isoformat(), meeting.time or ""),
        "- 时长：%s" % duration_text(meeting.duration),
        "- 参会人：%s" % "、".join(p.name for p in meeting.participants),
        "",
        "## 摘要",
    ]
    for index, topic in enumerate(minutes.summary, start=1):
        lines.append("### %d. %s" % (index, topic.topic))
        lines.extend("- %s" % point for point in topic.points)
        lines.append("")
    lines.append("## 决议")
    lines.extend("- %s" % decision.content for decision in minutes.decisions)
    lines.extend(["", "## 待办", "| 事项 | 负责人 | 截止 | 状态 | 证据 |", "| --- | --- | --- | --- | --- |"])
    for item in minutes.action_items:
        lines.append(
            "| %s | %s | %s | %s | %s |"
            % (
                item.task,
                owner_name(meeting, item.owner),
                due_text(item),
                "已完成" if item.status == "done" else "待处理",
                " ".join(item.evidence) or "-",
            )
        )
    lines.append("")
    if minutes.open_questions:
        lines.append("## 待确认问题")
        lines.extend("- %s" % question.content for question in minutes.open_questions)
        lines.append("")
    lines.append("## 风险")
    lines.extend("- %s" % risk.content for risk in minutes.risks)
    lines.extend(
        [
            "",
            "---",
            "> 由「会议纪要助手」生成。每条结论均绑定原声片段，可在应用内点击引用核对。",
        ]
    )
    return "\n".join(lines)


def actions_csv(meeting: Meeting, minutes: Minutes) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(["事项", "负责人", "截止时间", "状态", "是否待确认", "来源片段"])
    for item in minutes.action_items:
        writer.writerow(
            [
                item.task,
                owner_name(meeting, item.owner),
                due_text(item),
                "已完成" if item.status == "done" else "待处理",
                "是" if item.needs_review else "否",
                " ".join(item.evidence),
            ]
        )
    # BOM 让 Excel 正确识别 UTF-8
    return "\ufeff" + buffer.getvalue()


def result_json(meeting: Meeting, minutes: Minutes) -> str:
    payload = {
        "meeting": meeting.model_dump(mode="json"),
        "minutes": minutes.model_dump(mode="json"),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def conversation_markdown(conversation: Conversation) -> str:
    from datetime import datetime

    lines = [
        "# 对话记录",
        "",
        "导出时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "",
    ]
    for message in conversation.messages:
        who = "我" if message.role == "user" else "纪要助手"
        lines.append("**%s：** %s" % (who, message.content.replace("\n", "\n\n")))
        if message.evidence:
            lines.append("")
            lines.append("> 引用片段：%s" % "、".join(message.evidence))
        lines.append("")
    if len(lines) <= 4:
        lines.append("（本次会话还没有对话内容）")
    return "\n".join(lines)


def render(meeting: Meeting, minutes: Minutes, fmt: str) -> str:
    if fmt == "md":
        return minutes_markdown(meeting, minutes)
    if fmt == "csv":
        return actions_csv(meeting, minutes)
    return result_json(meeting, minutes)


@router.get("/{meeting_id}")
def export_meeting(
    meeting_id: str,
    format: str = Query(default="md", pattern="^(md|csv|json)$"),
    ctx: ToolContext = Depends(get_context),
) -> Response:
    meeting = MeetingRepo(ctx.conn).get(meeting_id)
    minutes = MinutesRepo(ctx.conn).get_with_overrides(meeting_id)
    if meeting is None or minutes is None:
        raise HTTPException(status_code=404, detail="会议或纪要不存在")

    media_type, suffix = FORMATS[format]
    body = render(meeting, minutes, format)
    filename = "%s_%s" % (meeting.title, suffix)
    if ctx.settings is not None:
        write_export(ctx.settings.export_dir, filename, body)
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": content_disposition(filename)},
    )
