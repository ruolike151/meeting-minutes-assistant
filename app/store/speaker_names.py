"""把已入库的发言人姓名改成字母代号（2026-09-15 定稿的一次性维护脚本逻辑）。

为什么需要它：改名改的是**代码与语料**，已经写进 SQLite 的 `participants.name`
与声纹库 `speakers.name` 还是旧值。删 `data/` 重来能解决，但会一起丢掉已注册的声纹
与已处理的会议，所以这里提供一条**幂等**的原地改名路径。

映射规则（按 speaker_id 优先，其次按旧姓名兜底）：

* 产品样例库：`p_chen`→A、`p_li`→B、`p_wang`→C、`p_zhao`→D、`p_sun`→E
* 真实录音语料：`p_pm`→A、`p_ops`→B、`p_design`→C、`p_dev`→D

只改显示名，不动 id、不动片段与纪要里的证据引用。
"""

from __future__ import annotations

import sqlite3
import re

# 按 speaker_id 映射（首选，稳定）
SPEAKER_CODES: dict[str, str] = {
    "p_chen": "A",
    "p_li": "B",
    "p_wang": "C",
    "p_zhao": "D",
    "p_sun": "E",
    "p_pm": "A",
    "p_ops": "B",
    "p_design": "C",
    "p_dev": "D",
}

# 兜底：老库里如果只有姓名（或 id 变过），按姓名映射
LEGACY_NAME_CODES: dict[str, str] = {
    "陈静": "A",
    "李明": "B",
    "王强": "C",
    "赵磊": "D",
    "孙悦": "E",
    "项目经理": "A",
    "项目负责人": "A",
    "运营专员": "B",
    "市场专员": "B",
    "UI设计师": "C",
    "设计专员": "C",
    "前端开发": "D",
    "执行专员": "D",
}


def code_for(speaker_id: str | None, name: str | None) -> str | None:
    """返回该发言人应有的代号；无法判断时返回 None（保持原样）。"""
    if speaker_id and speaker_id in SPEAKER_CODES:
        return SPEAKER_CODES[speaker_id]
    if name and name in LEGACY_NAME_CODES:
        return LEGACY_NAME_CODES[name]
    return None


def rename_speakers(conn: sqlite3.Connection, *, dry_run: bool = False) -> dict:
    """原地改名。返回 `{"participants": n, "speakers": n, "messages": n, "skipped": [...]}`。

    历史对话（`messages.content` / `messages.answer`）里的旧人名也一起替换：
    会话列表的标题取自第一条用户消息，不换的话"改完名还能看到姓名"。
    """
    result = {"participants": 0, "speakers": 0, "messages": 0, "skipped": []}

    rows = conn.execute(
        "SELECT meeting_id, speaker_id, name FROM participants"
    ).fetchall()
    for row in rows:
        code = code_for(row["speaker_id"], row["name"])
        if not code or code == row["name"]:
            continue
        if not dry_run:
            conn.execute(
                "UPDATE participants SET name = ? WHERE meeting_id = ? AND speaker_id = ?",
                (code, row["meeting_id"], row["speaker_id"]),
            )
        result["participants"] += 1

    existing = {
        row["name"] for row in conn.execute("SELECT name FROM speakers").fetchall()
    }
    for row in conn.execute("SELECT id, name FROM speakers").fetchall():
        code = code_for(row["id"], row["name"])
        if not code or code == row["name"]:
            continue
        if code in existing:
            # 声纹库 name 是唯一键：撞名就跳过并报告，不覆盖已有声纹
            result["skipped"].append(
                {"id": row["id"], "name": row["name"], "reason": "代号 %s 已被占用" % code}
            )
            continue
        if not dry_run:
            conn.execute("UPDATE speakers SET name = ? WHERE id = ?", (code, row["id"]))
        existing.discard(row["name"])
        existing.add(code)
        result["speakers"] += 1

    for row in conn.execute("SELECT id, content, answer FROM messages").fetchall():
        content = row["content"] or ""
        answer = row["answer"]
        new_content = replace_names(content)
        new_answer = replace_names(answer) if answer else answer
        if new_content == content and new_answer == answer:
            continue
        if not dry_run:
            conn.execute(
                "UPDATE messages SET content = ?, answer = ? WHERE id = ?",
                (new_content, new_answer, row["id"]),
            )
        result["messages"] += 1

    if not dry_run:
        conn.commit()
    return result


def replace_names(text: str) -> str:
    """把文本里的旧姓名/岗位称呼替换成字母代号（按最长匹配优先，避免半截替换）。

    代号后面紧跟中文时补一个空格（"王强一共…" → "C 一共…"），
    否则中英混排会挤成"C一共…"，读起来别扭。
    """
    for name in sorted(LEGACY_NAME_CODES, key=len, reverse=True):
        if name not in text:
            continue
        code = LEGACY_NAME_CODES[name]
        text = re.sub(re.escape(name) + r"(?=[\u4e00-\u9fff])", code + " ", text)
        text = text.replace(name, code)
    return text


__all__ = [
    "LEGACY_NAME_CODES",
    "SPEAKER_CODES",
    "code_for",
    "rename_speakers",
    "replace_names",
]
