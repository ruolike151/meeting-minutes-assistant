"""意图路由与范围解析（《技术方案》7.3 的 route / resolve_scope 节点）。

这一段是整个项目与「一个大 prompt 出摘要」的分界线：路由结果是显式的枚举，
所以「这句话该走哪条路」可以被单元测试直接断言（约束 C1）。

实现策略是「规则优先、模型兜底」：M1 全部走规则，覆盖不了时落到 content 检索，
再由检索层的拒答阈值决定是否明说「没找到」。M3 接入模型时保留同一套规则作为
兜底（文档 7.3：结构化输出 + 规则兜底），因此这里的接口不需要变。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.models import AnswerMode, Intent, Meeting
from app.retrieval.tokenize import STOPWORDS

# 规则表按「先具体后宽泛」排列，顺序本身就是优先级
SUMMARY_RE = re.compile(r"^(总结|汇总|概括|梳理)")
SPEAKER_TASK_RE = re.compile(r"(答应|承诺|负责|待办|做了哪些|哪些事|任务|跟进)")
WHO_RE = re.compile(r"谁(提|说|讲|负责|跟|来|发现|建议)")
RISK_RE = re.compile(r"(风险|隐患)")
OPEN_RE = re.compile(r"(没定|未定|待确认|悬而未决|没结论|没闭环|遗留)")
ACTION_RE = re.compile(r"(待办|要做什么|任务|谁要|跟进)")
DECISION_RE = re.compile(r"(定了什么|决议|结论|拍板|确定|通过)")


@dataclass(frozen=True)
class Scope:
    meeting_ids: list[str] = field(default_factory=list)
    narrowed: bool = False          # 问题里明确提到了某场会议
    ambiguous: bool = False
    source: str = "library"         # explicit | active | library


@dataclass(frozen=True)
class RouteResult:
    intent: Intent
    answer_mode: AnswerMode
    scope: Scope
    speaker_id: str | None = None
    speaker_name: str | None = None
    reason: str = ""


def title_keys(meeting: Meeting) -> list[str]:
    """会议标题去掉括号补充说明后的关键词。"""
    return [re.split(r"[（(]", meeting.title)[0]]


def _title_mentioned(meeting: Meeting, user_input: str) -> bool:
    for key in title_keys(meeting):
        for index in range(len(key) - 1):
            gram = key[index:index + 2]
            if gram in STOPWORDS:
                continue
            if gram in user_input:
                return True
    return False


def resolve_scope(
    user_input: str,
    meetings: list[Meeting],
    active_meeting_id: str | None = None,
) -> Scope:
    """确定查询范围。

    顺序即优先级：问题里点名的会议 > 会话当前锁定会议 > 全部会议。
    「这场会」这类限定语因此变成确定性的范围，而不是靠语义碰运气。
    """
    mentioned = [m for m in meetings if _title_mentioned(m, user_input)]
    if len(mentioned) == 1:
        return Scope(meeting_ids=[mentioned[0].id], narrowed=True, source="explicit")
    if len(mentioned) > 1:
        return Scope(
            meeting_ids=[m.id for m in mentioned], narrowed=True, ambiguous=True,
            source="explicit",
        )
    if active_meeting_id:
        return Scope(meeting_ids=[active_meeting_id], source="active")
    return Scope(meeting_ids=[m.id for m in meetings], source="library")


def find_speaker(user_input: str, meetings: list[Meeting]) -> tuple[str | None, str | None]:
    """在问题里找发言人姓名，返回 (speaker_id, name)。"""
    for meeting in meetings:
        for participant in meeting.participants:
            if participant.name and participant.name in user_input:
                return participant.id, participant.name
    return None, None


def route(
    user_input: str,
    *,
    meetings: list[Meeting],
    active_meeting_id: str | None = None,
) -> RouteResult:
    """规则式意图路由。返回的 reason 会进 trace，便于观测与回归。"""
    text = (user_input or "").strip()
    scope = resolve_scope(text, meetings, active_meeting_id)

    def result(intent: Intent, mode: AnswerMode, reason: str, **extra) -> RouteResult:
        return RouteResult(
            intent=intent, answer_mode=mode, scope=scope, reason=reason, **extra
        )

    # 1. 泛指「总结 / 汇总」且范围不唯一时先澄清，不猜是哪场会
    if SUMMARY_RE.search(text) and len(scope.meeting_ids) > 1 and not scope.narrowed:
        return result("query_content", "clarify", "rule:summary_ambiguous")

    # 2. 点名发言人
    speaker_id, speaker_name = find_speaker(text, meetings)
    if speaker_id:
        if SPEAKER_TASK_RE.search(text):
            return result(
                "query_speaker", "speaker_tasks", "rule:speaker_tasks",
                speaker_id=speaker_id, speaker_name=speaker_name,
            )
        return result(
            "query_speaker", "speaker_speech", "rule:speaker_speech",
            speaker_id=speaker_id, speaker_name=speaker_name,
        )

    # 3. 归属类问题：需要检索片段才能回答
    if WHO_RE.search(text):
        return result("query_content", "attribution", "rule:who")

    # 4. 结构化问题：直接读纪要，不需要检索
    if RISK_RE.search(text):
        return result("query_content", "risks", "rule:risks")
    if OPEN_RE.search(text):
        return result("query_content", "open_questions", "rule:open_questions")
    if ACTION_RE.search(text):
        return result("query_content", "actions", "rule:actions")
    if DECISION_RE.search(text):
        return result("query_content", "decisions", "rule:decisions")

    # 5. 兜底：片段级内容检索，检索不到就走拒答
    return result("query_content", "content", "rule:content")
