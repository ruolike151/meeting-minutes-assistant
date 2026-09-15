"""离线固定数据后端（《技术方案》14.1，约束 C3）。

两个角色：

1. 测试与 CI 的默认后端（`MA_PERCEPTION_BACKEND` 默认就是 offline）；
2. 真实后端（local）不可用时的兜底，保证演示不会因为少装一个依赖而开天窗。

它实现与 local 后端完全相同的 `PerceptionBackend` 协议（`base.py`），
因此契约测试对两者跑同一组断言。音频内容在这里被刻意忽略：
固定脚本与上传的文件无关，所以离线后端不需要 ffmpeg，也不需要任何模型。
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
from app.perception.base import ProgressCallback, RawSegment, SpeakerTurn

# 前端展示阶段进度用的阶段（保持 M1 的文案，界面与原型一致）
STAGES = [
    "音频标准化 · 16kHz 单声道",
    "语音转写 · WhisperX",
    "声纹识别与身份对齐 · ECAPA-TDNN",
    "生成结构化纪要 · Agent",
]

_SPEAKERS = [
    ("p_zhao", "D", "项目负责人"),
    ("p_chen", "A", "运营"),
    ("p_wang", "C", "前端开发"),
    ("p_li", "B", "设计"),
]

# (片段 ID, 起, 止, 说话人, 声纹置信度, 文本)
_TEMPLATE_SEGMENTS = [
    ("s1", 5, 58, "p_zhao", 0.96, "今天对齐一下双周进度，重点看支付模块这块。"),
    ("s2", 58, 240, "p_chen", 0.93,
     "支付这边用户反馈有一个比较集中的问题，退款到账时间太长，平均要三天。"),
    ("s3", 240, 520, "p_wang", 0.94,
     "这个是因为退款走的是定时任务批处理，一天只跑一次。如果改成实时回调，当天就能到账，开发量大概三天。"),
    ("s4", 520, 700, "p_zhao", 0.97,
     "三天可以做，那就改。B 你那边配合出一版退款状态的展示文案。"),
    ("s5", 700, 800, "p_li", 0.95, "可以，我明天之前给。"),
    ("s6", 800, 1080, "p_zhao", 0.96,
     "好。另外提醒一下，这次改动涉及资金链路，上线前必须过一轮完整的回归测试，这个不能省。"),
]


class OfflinePerception:
    """把音频路径换成一份固定样例会议。"""

    name = "offline"

    def stages(self) -> list[str]:
        return list(STAGES)

    def transcribe(self, audio_path: str | Path) -> list[RawSegment]:
        """返回固定脚本的转写片段（不看音频内容）。"""
        return [
            RawSegment(start=start, end=end, text=text, confidence=1.0)
            for _, start, end, _, _, text in _TEMPLATE_SEGMENTS
        ]

    def identify_speakers(
        self, audio_path: str | Path, segments: list[RawSegment]
    ) -> list[SpeakerTurn]:
        """离线后端的「声纹匹配」就是固定脚本里写死的说话人与置信度。"""
        turns: list[SpeakerTurn] = []
        for (_, _, _, speaker_id, confidence, _), segment in zip(_TEMPLATE_SEGMENTS, segments):
            turns.append(
                SpeakerTurn(
                    start=segment.start,
                    end=segment.end,
                    speaker_id=speaker_id,
                    confidence=confidence,
                    status="accepted",
                )
            )
        return turns

    def process(
        self,
        audio_path: str | Path,
        *,
        meeting_id: str,
        title: str,
        today: date | None = None,
        time_text: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> tuple[Meeting, list[Segment]]:
        """产出（会议, 片段）；片段 ID 与固定脚本一致，供纪要证据引用。"""
        raws = self.transcribe(audio_path)
        turns = self.identify_speakers(audio_path, raws)
        segments = [
            Segment(
                id=template[0],
                meeting_id=meeting_id,
                start=raw.start,
                end=raw.end,
                speaker_id=turn.speaker_id,
                speaker_confidence=turn.confidence,
                text=raw.text,
            )
            for template, raw, turn in zip(_TEMPLATE_SEGMENTS, raws, turns)
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
        if progress is not None:
            # 离线后端是瞬时完成的，这里逐条上报阶段，让前端仍能看到完整流水线；
            # 演示节奏由 worker 里的 MA_STAGE_DELAY 控制（真实后端由模型耗时决定）。
            for index, label in enumerate(self.stages()):
                progress(index, label)
        return meeting, segments

    def placeholder_minutes(self, meeting_id: str) -> Minutes | None:
        """固定脚本自带"标准答案"纪要（M3 起由 LLM 生成，本方法返回值不再使用）。"""
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