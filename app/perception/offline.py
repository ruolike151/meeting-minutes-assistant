"""离线固定数据后端（《技术方案》5、14.1）。

为什么需要它：约束 C3 要求「无 GPU、无模型 Key 也要跑通全部测试」。
离线后端把「一段音频」映射成一份固定的片段与纪要，
于是上传 -> 阶段进度 -> 纪要 -> 追问 这条链路在任何人机器上都能验证。

M2 接入真实 WhisperX / ECAPA-TDNN 后，本文件保留为测试后端，
上层只依赖 `process()` 的返回结构，不感知下面是模型还是固定数据。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.domain.models import (
    ActionItem,
    Decision,
    Meeting,
    Minutes,
    OpenQuestion,
    Participant,
    Risk,
    Segment,
    SummaryTopic,
)

# 前端展示阶段进度用的四个阶段（对应原型里的处理流水线）
STAGES = [
    "音频标准化 · 16kHz 单声道",
    "语音转写 · WhisperX",
    "声纹识别与身份对齐 · ECAPA-TDNN",
    "生成结构化纪要 · Agent",
]

_SPEAKERS = [
    ("p_zhao", "赵磊", "项目负责人"),
    ("p_chen", "陈静", "运营"),
    ("p_wang", "王强", "前端开发"),
    ("p_li", "李明", "设计"),
]

_TEMPLATE_SEGMENTS = [
    ("s1", 5, 58, "p_zhao", 0.96, "今天对齐一下双周进度，重点看支付模块这块。"),
    ("s2", 58, 240, "p_chen", 0.93,
     "支付这边用户反馈有一个比较集中的问题，退款到账时间太长，平均要三天。"),
    ("s3", 240, 520, "p_wang", 0.94,
     "这个是因为退款走的是定时任务批处理，一天只跑一次。如果改成实时回调，当天就能到账，开发量大概三天。"),
    ("s4", 520, 700, "p_zhao", 0.97,
     "三天可以做，那就改。李明你那边配合出一版退款状态的展示文案。"),
    ("s5", 700, 800, "p_li", 0.95, "可以，我明天之前给。"),
    ("s6", 800, 1080, "p_zhao", 0.96,
     "好。另外提醒一下，这次改动涉及资金链路，上线前必须过一轮完整的回归测试，这个不能省。"),
]


class OfflinePerception:
    """把音频路径换成一份固定样例会议。"""

    name = "offline"

    def transcribe(self, audio_path: str | Path) -> list[Segment]:
        """返回固定片段。真实后端在这里调用 ASR。"""
        return [
            Segment(
                id=seg_id,
                meeting_id="",
                start=start,
                end=end,
                speaker_id=speaker_id,
                speaker_confidence=confidence,
                text=text,
            )
            for seg_id, start, end, speaker_id, confidence, text in _TEMPLATE_SEGMENTS
        ]

    def process(
        self,
        audio_path: str | Path,
        *,
        meeting_id: str,
        title: str,
        today: date | None = None,
        time_text: str | None = None,
    ) -> tuple[Meeting, list[Segment], Minutes]:
        """产出（会议, 片段, 纪要）三元组，并补齐 meeting_id。"""
        segments = [
            segment.model_copy(update={"meeting_id": meeting_id})
            for segment in self.transcribe(audio_path)
        ]
        meeting = Meeting(
            id=meeting_id,
            title=title,
            date=today or date.today(),
            time=time_text or "00:00",
            duration=float(segments[-1].end) if segments else 0.0,
            status="processed",
            audio_file=Path(audio_path).name,
            participants=[
                Participant(id=speaker_id, name=name, role=role)
                for speaker_id, name, role in _SPEAKERS
            ],
        )
        return meeting, segments, self.minutes(meeting_id)

    def minutes(self, meeting_id: str) -> Minutes:
        return Minutes(
            meeting_id=meeting_id,
            summary=[
                SummaryTopic(
                    topic="退款到账时长问题",
                    evidence=["s2", "s3"],
                    points=[
                        "用户反馈退款平均 3 天到账",
                        "根因是退款走定时任务批处理，一天执行一次",
                        "改为实时回调可当天到账，开发量约 3 天",
                    ],
                ),
                SummaryTopic(
                    topic="后续动作",
                    evidence=["s4", "s5", "s6"],
                    points=[
                        "退款链路改为实时回调",
                        "设计侧补充退款状态展示文案",
                        "上线前必须完成资金链路全量回归测试",
                    ],
                ),
            ],
            decisions=[
                Decision(
                    id="d1",
                    content="退款到账由定时批处理改为实时回调方案，开发量按 3 天评估。",
                    evidence=["s3", "s4"],
                    confidence=0.93,
                )
            ],
            action_items=[
                ActionItem(
                    id="a1",
                    task="将退款到账改造为实时回调（约 3 天）",
                    owner="p_wang",
                    owner_source="explicit",
                    status="pending",
                    evidence=["s3", "s4"],
                    confidence=0.6,
                    needs_review=True,
                    review_reason="会上未约定具体的交付日期",
                ),
                ActionItem(
                    id="a2",
                    task="输出退款状态展示文案",
                    owner="p_li",
                    owner_source="explicit",
                    due_text="明天之前",
                    status="pending",
                    evidence=["s4", "s5"],
                    confidence=0.9,
                    needs_review=False,
                ),
                ActionItem(
                    id="a3",
                    task="上线前完成资金链路全量回归测试",
                    owner=None,
                    owner_source="missing",
                    status="pending",
                    evidence=["s6"],
                    confidence=0.55,
                    needs_review=True,
                    review_reason="会上强调必须执行，但未指定负责人",
                ),
            ],
            open_questions=[
                OpenQuestion(id="q1", content="退款链路改造的提测时间未明确。", evidence=["s6"])
            ],
            risks=[
                Risk(
                    id="r1",
                    content="改动涉及资金链路，若上线前未完成全量回归测试，存在资损风险。",
                    evidence=["s6"],
                )
            ],
        )


def title_from_filename(filename: str) -> str:
    """用文件名当会议标题，去掉扩展名并限长。"""
    stem = Path(filename or "会议录音").stem
    return (stem or "会议录音")[:24]
