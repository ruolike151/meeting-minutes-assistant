"""评测数据集契约（M5 T0）。

数据落在 `samples/annotations/m5/`，一个文件一类标注：

| 文件 | 用途 |
| --- | --- |
| `retrieval_gold.json` | 检索：query → 金标片段（空表示应拒答）+ 跨会议允许集合 |
| `route_gold.json` | 路由：query → 期望 intent / answer_mode / scope，可带多轮上下文 |
| `qa_gold.json` | 问答：query → 金标要点 + 允许引用的片段集合 |
| `minutes_gold.json` | 纪要：meeting_id → 待办 / 决议 / 风险 金标 |
| `review_gold.json` | 协同：待确认条目 → 期望处理动作 |
| `real_meetings.json` | 真实会议语料：元数据 + 转写片段 + 纪要金标（由 `scripts/build_eval_corpus.py` 合成） |

三条硬规则（`check_against_db` 强制）：

1. **金标片段必须真实存在**——写错一个 ID 会让指标静默走低，必须当场报错；
2. **应拒答的用例不能带金标**，带金标的用例不能标 `must_refuse`；
3. **每场会议至少有一条检索与一条问答用例**，否则那场会议根本没被评测到。

数据来源（M5 决策 ①=B）：真实会议录音（脱敏）由用户提供后按同一 schema 补入；
素材到位前，标注基于仓库内的样例会议，报告的"数据源"字段会如实标注。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

SECTION_FILES = {
    "retrieval": "retrieval_gold.json",
    "routes": "route_gold.json",
    "qa": "qa_gold.json",
    "minutes": "minutes_gold.json",
    "review": "review_gold.json",
    "meetings": "real_meetings.json",
}


class RetrievalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    gold_segments: list[str] = Field(default_factory=list)   # "meeting_id:segment_id"
    gold_meetings: list[str] = Field(default_factory=list)   # 跨会议问题允许命中的会议
    note: str = ""

    @property
    def answerable(self) -> bool:
        return bool(self.gold_segments)


class RouteCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    intent: str                                  # process_audio | query_content | query_speaker | unknown
    answer_mode: str                             # structured / content / clarify / refuse 等
    scope: list[str] = Field(default_factory=list)
    context: list[str] = Field(default_factory=list)  # 多轮：前置轮次
    meeting_id: str | None = None                # 界面当前锁定的会议
    note: str = ""


class QACase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    gold_points: list[str] = Field(default_factory=list)
    allowed_segments: list[str] = Field(default_factory=list)
    must_refuse: bool = False
    meeting_id: str | None = None
    note: str = ""


class GoldAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str
    owner: str | None = None
    due: str | None = None
    evidence: list[str] = Field(default_factory=list)


class GoldDecision(BaseModel):
    """决议金标：内容 + 证据片段（与待办同口径，便于按证据对齐而不是抠字面）。"""

    model_config = ConfigDict(extra="forbid")

    content: str
    evidence: list[str] = Field(default_factory=list)


class MinutesGold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meeting_id: str
    action_items: list[GoldAction] = Field(default_factory=list)
    decisions: list[GoldDecision] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class ReviewGold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str                                  # "meeting_id:item_id"
    kind: str = "action_item"
    expected_action: str = "confirm"              # confirm | dismiss | edit
    note: str = ""


class RealParticipant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    role: str | None = None
    label: str = ""                               # 转写稿里的说话人标签（A/B/C/D）


class RealSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    start: float
    end: float
    speaker_id: str | None = None                 # 多人合说（GROUP）为 None
    speaker_confidence: float = 1.0
    text: str
    note: str = ""


class RealGoldAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str
    owner: str | None = None
    due: str | None = None
    evidence: list[str] = Field(default_factory=list)


class RealMinutes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[GoldDecision] = Field(default_factory=list)
    action_items: list[RealGoldAction] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class RealMeeting(BaseModel):
    """真实会议语料：转写片段 + 纪要金标，由评测运行器灌入评测专用的数据目录。"""

    model_config = ConfigDict(extra="forbid")

    meeting_id: str
    title: str
    date: str
    time: str | None = None
    duration: float = 0.0
    audio_file: str | None = None
    participants: list[RealParticipant] = Field(default_factory=list)
    segments: list[RealSegment] = Field(default_factory=list)
    minutes: RealMinutes = Field(default_factory=RealMinutes)


class EvalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = ""
    updated_at: str = ""
    source: str = "sample"                        # sample | real_meeting
    notes: list[str] = Field(default_factory=list)
    retrieval: list[RetrievalCase] = Field(default_factory=list)
    routes: list[RouteCase] = Field(default_factory=list)
    qa: list[QACase] = Field(default_factory=list)
    minutes: list[MinutesGold] = Field(default_factory=list)
    review: list[ReviewGold] = Field(default_factory=list)
    meetings: list[RealMeeting] = Field(default_factory=list)

    def size(self) -> dict:
        return {
            "retrieval": len(self.retrieval),
            "routes": len(self.routes),
            "qa": len(self.qa),
            "minutes": len(self.minutes),
            "review": len(self.review),
            "real_meetings": len(self.meetings),
            "real_segments": sum(len(item.segments) for item in self.meetings),
        }


def dataset_dir(settings) -> Path:
    """数据集目录（可用 `MA_EVAL_DATASET_DIR` 覆盖）。"""
    return Path(settings.eval_dataset_dir)


def load_dataset(path: str | Path | None = None, *, settings=None) -> EvalDataset:
    """装载五个标注文件并做交叉校验（缺文件即报错，不静默跳过空集）。"""
    base = Path(path) if path is not None else dataset_dir(settings)
    sections: dict[str, list] = {}
    version = ""
    updated_at = ""
    source = "sample"
    notes: list[str] = []
    for key, filename in SECTION_FILES.items():
        file_path = base / filename
        if not file_path.is_file():
            raise FileNotFoundError("缺少标注文件：%s" % file_path)
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        if not payload.get("version"):
            raise ValueError("%s 缺少 version（数据集必须可追溯到某一版）" % filename)
        version = version or payload["version"]
        if payload["version"] != version:
            raise ValueError("%s 的版本与其它标注文件不一致" % filename)
        updated_at = updated_at or payload.get("updated_at", "")
        source = payload.get("source", source)
        notes.extend(payload.get("notes", []))
        sections[key] = payload.get("cases", [])
    dataset = EvalDataset(
        version=version,
        updated_at=updated_at,
        source=source,
        notes=notes,
        retrieval=[RetrievalCase.model_validate(item) for item in sections["retrieval"]],
        routes=[RouteCase.model_validate(item) for item in sections["routes"]],
        qa=[QACase.model_validate(item) for item in sections["qa"]],
        minutes=[MinutesGold.model_validate(item) for item in sections["minutes"]],
        review=[ReviewGold.model_validate(item) for item in sections["review"]],
        meetings=[RealMeeting.model_validate(item) for item in sections["meetings"]],
    )
    # 真实会议的纪要金标只在 real_minutes_gold.json 里写一次，
    # 这里映射成统一的 MinutesGold，让纪要评测与其他会议走同一条路径。
    dataset.minutes.extend(
        MinutesGold(
            meeting_id=case.meeting_id,
            action_items=[
                GoldAction(
                    task=item.task,
                    owner=item.owner,
                    due=item.due,
                    # 真实会议语料里的证据是会议内的局部 ID（s3），
                    # 统一成全局键（m_real_1:s3），与其它金标口径一致
                    evidence=["%s:%s" % (case.meeting_id, key) for key in item.evidence],
                )
                for item in case.minutes.action_items
            ],
            decisions=[
                GoldDecision(
                    content=item.content,
                    evidence=["%s:%s" % (case.meeting_id, key) for key in item.evidence],
                )
                for item in case.minutes.decisions
            ],
            risks=list(case.minutes.risks),
        )
        for case in dataset.meetings
    )
    return dataset


def check_against_db(dataset: EvalDataset, conn: sqlite3.Connection) -> list[str]:
    """与库内数据对账，返回问题列表（空列表表示通过）。"""
    problems: list[str] = []
    meetings = {
        row["id"] for row in conn.execute("SELECT id FROM meetings").fetchall()
    }
    segments = {
        "%s:%s" % (row["meeting_id"], row["id"])
        for row in conn.execute("SELECT meeting_id, id FROM segments").fetchall()
    }
    covered: set[str] = set()

    for case in dataset.retrieval:
        if case.gold_segments and not case.answerable:
            problems.append("检索用例标注异常：%s" % case.query)
        for key in case.gold_segments:
            if key not in segments:
                problems.append("检索金标片段不存在：%s（%s）" % (key, case.query))
            covered.add(key.split(":")[0])
        for meeting_id in case.gold_meetings:
            if meeting_id not in meetings:
                problems.append("跨会议金标会议不存在：%s（%s）" % (meeting_id, case.query))

    for case in dataset.qa:
        if case.must_refuse and case.gold_points:
            problems.append("标注矛盾：既要求拒答又给金标要点（%s）" % case.query)
        if not case.must_refuse and not case.gold_points:
            problems.append("问答用例缺少金标要点：%s" % case.query)
        for key in case.allowed_segments:
            if key not in segments:
                problems.append("问答允许片段不存在：%s（%s）" % (key, case.query))

    for gold in dataset.minutes:
        if gold.meeting_id not in meetings:
            problems.append("纪要金标会议不存在：%s" % gold.meeting_id)
        for action in gold.action_items:
            for key in action.evidence:
                if key not in segments:
                    problems.append("待办金标证据不存在：%s（%s）" % (key, gold.meeting_id))

    for case in dataset.routes:
        for meeting_id in case.scope:
            if meeting_id == "*":
                continue  # 通配：表示"当前库里的全部会议"，由评测运行器展开
            if meeting_id not in meetings:
                problems.append("路由金标会议不存在：%s（%s）" % (meeting_id, case.query))

    # 真实会议语料的内部一致性：片段 ID 唯一、纪要金标只引用自己的片段
    for meeting in dataset.meetings:
        segment_ids = [segment.id for segment in meeting.segments]
        if not segment_ids:
            problems.append("真实会议没有片段：%s" % meeting.meeting_id)
        if len(set(segment_ids)) != len(segment_ids):
            problems.append("真实会议片段 ID 重复：%s" % meeting.meeting_id)
        participant_ids = {item.id for item in meeting.participants}
        for action in meeting.minutes.action_items:
            if action.owner and action.owner not in participant_ids:
                problems.append(
                    "真实会议待办负责人不在参会人里：%s（%s）"
                    % (action.owner, meeting.meeting_id)
                )
            for key in action.evidence:
                if key not in segment_ids:
                    problems.append(
                        "真实会议待办证据不存在：%s:%s" % (meeting.meeting_id, key)
                    )

    # 覆盖度：每场会议至少出现在一条检索与一条问答用例里
    qa_covered = {
        key.split(":")[0] for case in dataset.qa for key in case.allowed_segments
    }
    for meeting_id in sorted(meetings):
        if meeting_id not in covered:
            problems.append("会议 %s 没有任何检索用例" % meeting_id)
        if meeting_id not in qa_covered:
            problems.append("会议 %s 没有任何问答用例" % meeting_id)
    return problems


__all__ = [
    "EvalDataset",
    "GoldAction",
    "MinutesGold",
    "QACase",
    "RealGoldAction",
    "RealMeeting",
    "RealMinutes",
    "RealParticipant",
    "RealSegment",
    "RetrievalCase",
    "ReviewGold",
    "RouteCase",
    "SECTION_FILES",
    "check_against_db",
    "dataset_dir",
    "load_dataset",
]
