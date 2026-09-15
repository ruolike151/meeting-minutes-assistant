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
from app.api.schemas import ReviewItem
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


def test_review_fields_used_by_ui_exist() -> None:
    """待确认区读的字段必须都在 ReviewItem DTO 里（M4 加了 kind / due / editable）。"""
    source = read("panel.js")
    card = function_body(source, "reviewCard")
    assert card, "panel.js 应该有一个 reviewCard 渲染函数"
    used = set(re.findall(r"\bitem\.(\w+)", card))
    missing = used - set(ReviewItem.model_fields)
    assert not missing, "待确认区读取了 ReviewItem 不存在的字段：%s" % sorted(missing)


def test_evidence_jump_supports_cross_meeting_and_audio() -> None:
    """证据回跳的两条 M4 能力必须在代码里真实存在（静态断言，防止被重构掉）。"""
    source = read("panel.js")
    jump = function_body(source, "openSegment")
    assert "loadLibrary" in jump, "跨会议引用需要先补齐会议库"
    assert "await loadDetail" in jump, "引用所在会议不在库时要能按需加载详情"
    raw = function_body(source, "renderRaw")
    assert "/audio" in raw and "panelAudio" in raw, "有原声时要渲染播放器"
    play = function_body(source, "playSegment")
    assert "currentTime" in play, "点击引用要定位到片段起点"


def test_frontend_stays_buildless() -> None:
    """无构建链路：脚本与样式都是本地文件，没有打包产物。

    字体是唯一允许的外链（设计令牌已定，M4 不改），因此这里只卡脚本与样式。
    """
    html = read("index.html")
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
    assert scripts, "页面应该通过本地脚本加载逻辑"
    assert all(not src.startswith(("http://", "https://")) for src in scripts), scripts
    styles = re.findall(r'<link[^>]+href="([^"]+\.css[^"]*)"', html)
    assert styles and all(
        not href.startswith(("http://", "https://")) for href in styles
    ), styles
    assert not (WEB / "dist").exists(), "不引入打包产物目录"


def test_scope_chip_can_switch_back_to_library() -> None:
    """提问范围标签：显示当前范围，且能一键切回「全部会议」（对话不丢）。

    用户反馈的原始问题就是"助手只能查一个对话吗"——原因是上传音频或点击会议后
    界面会把范围锁定到单场，而原来只有「新对话」能重置（会把对话一起清掉）。
    """
    html = read("index.html")
    for node_id in ("scopeChip", "scopeChipText", "scopeChipClear"):
        assert 'id="%s"' % node_id in html, "缺少范围标签节点：" + node_id
    chat = read("chat.js")
    scope_fn = function_body(chat, "setScope")
    assert "scopeChipText" in scope_fn, "setScope 要同步范围标签文案"
    assert "dataset.meetingId" in scope_fn, "范围标签要记录当前限定到哪场会议"
    app = read("app.js")
    assert "scopeChip" in app and "setScope(null)" in app, "app.js 要绑定范围标签的点击"


def test_conversation_switcher_is_wired() -> None:
    """侧栏的"对话"栏：节点存在、接口可用、点击能加载历史会话。"""
    html = read("index.html")
    for node_id in ("conversationList", "conversationCount", "convEmpty"):
        assert 'id="%s"' % node_id in html, "缺少会话列表节点：" + node_id
    api_source = read("api.js")
    assert "listConversations" in api_source and "getConversation" in api_source
    panel = read("panel.js")
    assert "renderConversationList" in panel and "ma:open-conversation" in panel
    chat = read("chat.js")
    assert "openConversation" in chat and "ma:open-conversation" in chat
    assert "loadConversations" in chat, "发完消息要刷新会话列表"
    assert "conversationList" in read("app.js"), "app.js 要绑定列表点击"


def test_audio_player_respects_availability() -> None:
    """原声不可播时不要渲染坏掉的播放器（用户反馈过这个问题）。"""
    raw = function_body(read("panel.js"), "renderRaw")
    assert "audio_available" in raw, "播放器要按 audio_available 判断"
    assert "没有可播放的原声文件" in raw, "不可播时要有说明文案"
    assert "playable" in raw


def test_store_initialises_messages_array() -> None:
    """`store.messages` 必须在 store 里初始化。

    浏览器实测抓到的 bug：只有点过「新建对话」才会给它赋值，
    于是"进页面直接提问"会撞上 undefined.push（首屏必崩，但只在没点新对话时出现）。
    """
    source = read("store.js")
    assert "messages: []" in source, "store 必须初始化 messages: []"
