"""证据与要点的判定（M5 T4）。

三个判定器，成本与可信度递增：

1. **规则版（默认，零成本）**：引用片段存在 + 结论与片段的关键词覆盖率达标；
2. **LLM 判定版**：把「结论 + 引用片段」成对喂给模型，输出 supported / unsupported；
3. **人工抽样**：从 LLM 判定的两类里各抽若干条人工复核，用来校准前两个。

规则版的假阳性是已知的——"引用存在"不等于"支撑结论"。所以报告里三个数字一起给，
再附一致率（Cohen's kappa），而不是只报一个好看的数。
"""

from __future__ import annotations

import random
import re

from app.llm.client import ChatMessage, LLMClient
from app.retrieval.tokenize import content_tokens

# 结论里的词有多少比例能在引用片段里找到（低于该值判为"引了但没支撑"）
SUPPORT_COVERAGE_THRESHOLD = 0.34


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in content_tokens(text or "")}


def rule_support(answer_text: str, segment_text: str) -> tuple[bool, float]:
    """规则判定：结论的关键词在片段里的覆盖率。返回 (是否被支撑, 覆盖率)。"""
    wanted = _tokens(answer_text)
    if not wanted:
        return False, 0.0
    have = _tokens(segment_text)
    coverage = len(wanted & have) / len(wanted)
    return coverage >= SUPPORT_COVERAGE_THRESHOLD, round(coverage, 4)


def rule_point_coverage(answer_text: str, gold_points: list[str]) -> tuple[int, int]:
    """要点覆盖（规则版）：金标要点的关键词有半数以上出现在回答里即算命中。"""
    if not gold_points:
        return 0, 0
    answer = _tokens(answer_text)
    hit = 0
    for point in gold_points:
        wanted = _tokens(point)
        if wanted and len(wanted & answer) / len(wanted) >= 0.5:
            hit += 1
    return hit, len(gold_points)


JUDGE_PROMPT = (
    "你是会议内容的核查员。判断「结论」是否被「引用片段」支撑。\n"
    "判定规则：\n"
    "1. 片段必须直接支持结论的核心事实，只沾边不算；\n"
    "2. 片段与结论矛盾、或只提到同一话题但没有给出结论内容 → unsupported；\n"
    "3. 只输出 JSON，不要解释。\n\n"
    '输出格式：{"supported": true} 或 {"supported": false}\n\n'
    "结论：{answer}\n引用片段：{segment}\n"
)


def build_judge_messages(answer_text: str, segment_text: str) -> list[ChatMessage]:
    """构造判定用的消息；提示词文件存在时优先用它（便于与产品口径对齐）。"""
    from app.agent.prompts import load_prompt

    try:
        template = load_prompt("judge")
    except Exception:  # noqa: BLE001 - 提示词文件缺失时退回内置模板
        template = JUDGE_PROMPT
    # 用 replace 而不是 str.format：提示词里包含 JSON 花括号，
    # format 会把 {"supported": ...} 当成占位符直接抛 KeyError。
    body = template.replace("{answer}", answer_text).replace("{segment}", segment_text)
    return [ChatMessage(role="user", content=body)]


def parse_supported(text: str) -> bool:
    """从模型输出里取判定结果（容忍 Markdown 代码块与空白）。"""
    match = re.search(r'"supported"\s*:\s*(true|false)', (text or "").lower())
    if match:
        return match.group(1) == "true"
    return False


def llm_support(
    client: LLMClient, pairs: list[tuple[str, str]], *, model: str = ""
) -> list[bool]:
    """逐对调用模型判定。单条异常按"不支持"处理，不让评测整体失败。"""
    results: list[bool] = []
    for answer_text, segment_text in pairs:
        try:
            result = client.complete(
                build_judge_messages(answer_text, segment_text), model=model or "judge"
            )
            text = getattr(result, "text", None)
            if text is None:
                text = getattr(result, "content", "")
            results.append(parse_supported(str(text)))
        except Exception:  # noqa: BLE001 - 单条失败不该终止整轮评测
            results.append(False)
    return results


def sample_for_review(
    judgements: list[bool], *, per_class: int = 15, seed: int = 7
) -> list[int]:
    """从正 / 负两类里各抽若干条做人工复核；样本不足时全取。固定种子可复现。"""
    positive = [index for index, value in enumerate(judgements) if value]
    negative = [index for index, value in enumerate(judgements) if not value]
    rng = random.Random(seed)
    picked = rng.sample(positive, min(per_class, len(positive)))
    picked += rng.sample(negative, min(per_class, len(negative)))
    return sorted(picked)


__all__ = [
    "JUDGE_PROMPT",
    "SUPPORT_COVERAGE_THRESHOLD",
    "build_judge_messages",
    "llm_support",
    "parse_supported",
    "rule_point_coverage",
    "rule_support",
    "sample_for_review",
]
