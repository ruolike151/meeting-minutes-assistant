"""证据校验（《技术方案》7.4「证据校验后置」）。

生成完不直接返回，而是把每条结论引用的片段再核一遍：

* 引用不存在的片段 → 直接丢掉；
* 一条本该有证据的结论如果引用全被丢光 → 标记 `warn`，不让无法核对的结论进最终稿。

这一步是把"带引用"从展示格式变成质量约束（约束 C4）。函数与框架、模型无关，
因此可以被 `verify_evidence` 节点直接调用，也可以被测试单独断言。
"""

from __future__ import annotations

from app.agent.answers import AnswerContext, Draft
from app.domain.models import AnswerBlock, AnswerItem, Citation


def verify_evidence(ctx: AnswerContext, draft: Draft) -> tuple[Draft, int, int]:
    """返回 (校验后的草稿, 丢弃的引用数, 无证据结论数)。"""
    dropped = 0
    unsupported = 0
    blocks: list[AnswerBlock] = []
    for block in draft.blocks:
        if block.kind == "note":
            blocks.append(block)
            continue
        items: list[AnswerItem] = []
        for item in block.items:
            keep = [
                citation
                for citation in item.citations
                if ctx.segment(citation.meeting_id, citation.segment_id) is not None
            ]
            dropped += len(item.citations) - len(keep)
            if item.citations and not keep:
                unsupported += 1
                item = item.model_copy(update={"warn": True})
            items.append(item.model_copy(update={"citations": keep}))
        blocks.append(block.model_copy(update={"items": items}))
    return Draft(lead=draft.lead, blocks=blocks, footnote=draft.footnote), dropped, unsupported


def flatten_citations(draft: Draft) -> list[Citation]:
    """去重后的引用清单：前端渲染与评测都用这一份。"""
    seen: set[str] = set()
    citations: list[Citation] = []
    for block in draft.blocks:
        for item in block.items:
            for citation in item.citations:
                key = citation.key
                if key in seen:
                    continue
                seen.add(key)
                citations.append(citation)
    return citations
