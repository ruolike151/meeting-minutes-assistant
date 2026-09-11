"""前端契约测试：界面读取的字段与调用的接口必须真实存在。

前端没有构建链路与类型检查，最容易出的问题是「后端改了字段名、前端静默渲染空白」。
这里用静态检查把这条缝堵上：
1. 模块之间的 import / export 必须对得上；
2. 界面读取的响应字段必须出现在对应的 Pydantic DTO 里。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.api.schemas import MeetingDetail
from app.domain.models import Answer, AnswerBlock, AnswerItem, Message

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

# `$` / `$$` 也是合法的导出名，因此不能只用 \w
EXPORT_PATTERN = re.compile(r"export\s+(?:async\s+)?(?:function|const)\s+([\w$]+)")
IMPORT_PATTERN = re.compile(
    r"import\s*\{([^}]*)\}\s*from\s*'(\./[\w.-]+)'"
    r"|import\s*\*\s*as\s+(\w+)\s+from\s*'(\./[\w.-]+)'"
)


def read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def exports_of(name: str) -> set[str]:
    return set(EXPORT_PATTERN.findall(read(name)))


def function_body(source: str, name: str) -> str:
    """取一个顶层函数的函数体，避免把同名参数的其他用法算进来。"""
    match = re.search(r"function\s+%s\s*\([^)]*\)\s*\{([\s\S]*?)\n\}" % name, source)
    return match.group(1) if match else ""


def test_relative_imports_resolve_to_real_exports() -> None:
    for path in sorted(WEB.glob("*.js")):
        source = path.read_text(encoding="utf-8")
        for named, module, namespace, namespace_module in IMPORT_PATTERN.findall(source):
            if namespace:
                assert (WEB / namespace_module.lstrip("./")).is_file(), namespace_module
                continue
            target = module.lstrip("./")
            assert (WEB / target).is_file(), "%s 引用了不存在的模块 %s" % (path.name, module)
            imported = {item.strip() for item in named.split(",") if item.strip()}
            missing = imported - exports_of(target)
            assert not missing, "%s 从 %s 导入了未导出的名字：%s" % (
                path.name,
                target,
                sorted(missing),
            )


def test_namespace_api_calls_exist() -> None:
    exported = exports_of("api.js")
    for name in ("app.js", "chat.js", "panel.js"):
        called = set(re.findall(r"\bapi\.(\w+)\s*\(", read(name)))
        missing = called - exported
        assert not missing, "%s 调用了 api.js 未导出的接口：%s" % (name, sorted(missing))


def test_detail_fields_used_by_ui_exist() -> None:
    allowed = set(MeetingDetail.model_fields)
    used: set[str] = set()
    for name in ("panel.js", "chat.js"):
        used.update(re.findall(r"\bdetail\.(\w+)", read(name)))
    missing = used - allowed
    assert not missing, "界面读取了 MeetingDetail 不存在的字段：%s" % sorted(missing)


def test_answer_fields_used_by_ui_exist() -> None:
    chat = read("chat.js")
    render_answer = function_body(chat, "renderAnswer")
    item_html = function_body(chat, "itemHtml")
    assert set(re.findall(r"\banswer\.(\w+)", render_answer)) <= set(Answer.model_fields)
    assert set(re.findall(r"\bblock\.(\w+)", render_answer)) <= set(AnswerBlock.model_fields)
    assert set(re.findall(r"\bitem\.(\w+)", item_html)) <= set(AnswerItem.model_fields)


def test_message_fields_used_by_ui_exist() -> None:
    """聊天流里读的 result.message.* 必须是后端 Message 的字段。

    注意 appendMessage 的入参是前端自己的渲染结构（role/html），
    不是后端的 Message DTO，所以不在这里检查。
    """
    used = set(re.findall(r"\bresult\.message\.(\w+)", read("chat.js")))
    assert used <= set(Message.model_fields), sorted(used - set(Message.model_fields))
