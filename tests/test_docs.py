#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""文档一致性测试。

校验《产品设计文档》与《技术方案》不漂移：技术选型一致、关键决策落到文档里、
章节结构完整、Markdown 格式没有会把段落渲染成标题的低级错误。

不依赖 pytest：项目要求"任何机器上都能跑通验证"，因此本文件同时支持
    python tests/test_docs.py
与
    pytest tests/test_docs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_DOC = ROOT / 'docs' / '产品设计文档.md'
TECH_DOC = ROOT / 'docs' / '技术方案.md'
PROTO_README = ROOT / 'prototype' / 'README.md'

PRODUCT_SECTIONS = [
    '## 1. 项目定位',
    '## 2. 目标用户与使用场景',
    '## 3. MVP 范围',
    '## 4. 整体流程',
    '## 5. 功能模块',
    '## 6. 数据结构（草案）',
    '## 7. 关键设计决策',
    '## 8. 评测与测试',
    '## 9. 技术栈',
    '## 10. 里程碑',
    '## 11. 待确认事项',
]

TECH_SECTIONS = [
    '## 1. 文档目的与范围',
    '## 2. 工程约束',
    '## 3. 总体架构',
    '## 4. 技术选型与理由',
    '## 5. 工程结构',
    '## 6. 数据契约与存储',
    '## 7. 编排层设计',
    '## 8. 工具层设计',
    '## 9. 检索层设计',
    '## 10. LLM 适配与结构化输出',
    '## 11. HTTP 接口',
    '## 12. 前端设计',
    '## 13. 可观测与成本',
    '## 14. 测试与评测',
    '## 15. 依赖与版本锁定',
    '## 16. 风险与权衡',
    '## 17. 里程碑',
]

# 两份文档必须同时提及的组件
STACK_COMPONENTS = [
    'FastAPI',
    'LangGraph',
    'langchain-core',
    'Pydantic',
    'FTS5',
    'RRF',
    'pytest',
    'Langfuse',
    'uv.lock',
]

TECH_KEYWORDS = [
    'StateGraph',
    'checkpointer',
    'interrupt',
    'RRF',
    'FakeLLMClient',
    'InMemorySaver',
    'override',
    '拒答',
]

FAILURES: list[str] = []
COUNT = 0


def check(name: str, cond: bool, detail: str = '') -> None:
    global COUNT
    COUNT += 1
    if cond:
        print('  ok   ' + name)
    else:
        msg = name + (' → ' + detail if detail else '')
        FAILURES.append(msg)
        print('  FAIL ' + msg)


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def section(text: str, heading: str) -> str:
    """取 heading 起始、到下一个同级或更高级标题之间的正文。"""
    lines = text.split('\n')
    level = len(heading) - len(heading.lstrip('#'))
    start = None
    for i, line in enumerate(lines):
        if line.startswith(heading):
            start = i + 1
            break
    if start is None:
        return ''
    for j in range(start, len(lines)):
        m = re.match(r'^(#{1,6})\s', lines[j])
        if m and len(m.group(1)) <= level:
            return '\n'.join(lines[start:j])
    return '\n'.join(lines[start:])


def rule_line_issues(text: str) -> list[str]:
    """裸 --- 紧跟正文时会被 Markdown 解析成标题下划线，属于格式缺陷。"""
    lines = text.split('\n')
    issues = []
    in_fence = False
    for i, line in enumerate(lines):
        if line.strip().startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.strip() == '---' and i > 0:
            prev = lines[i - 1].strip()
            if prev:
                issues.append('第 %d 行前缺少空行：%s' % (i, prev[:20]))
    return issues


def force_utf8_stdout() -> None:
    """Windows 控制台默认不是 UTF-8，先切一下再输出中文。"""
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


def run() -> list[str]:
    global COUNT
    COUNT = 0
    FAILURES.clear()
    force_utf8_stdout()

    print('[1] 文档存在性')
    check('产品设计文档存在', PRODUCT_DOC.is_file(), str(PRODUCT_DOC))
    check('技术方案存在', TECH_DOC.is_file(), str(TECH_DOC))
    check('原型 README 存在', PROTO_README.is_file(), str(PROTO_README))
    if FAILURES:
        return list(FAILURES)

    product = read(PRODUCT_DOC)
    tech = read(TECH_DOC)
    readme = read(PROTO_README)

    print('[2] 产品设计文档结构')
    for h in PRODUCT_SECTIONS:
        check('含章节 ' + h, h in product)

    print('[3] 技术方案结构')
    for h in TECH_SECTIONS:
        check('含章节 ' + h, h in tech)

    print('[4] 技术选型一致性')
    stack = section(product, '## 9. 技术栈')
    check('技术栈章节可定位', bool(stack))
    check('技术栈已改用 FastAPI', 'FastAPI' in stack)
    check('技术栈不再出现被否决的 Streamlit', 'Streamlit' not in stack)
    check('技术栈已改用 LangGraph', 'LangGraph' in stack)
    check('技术栈声明不用 AgentExecutor', 'AgentExecutor' in stack)
    check('技术栈记录检索层自研', '自研混合检索' in stack)
    check('技术栈记录依赖锁定', '锁定' in stack)
    for comp in STACK_COMPONENTS:
        check('技术方案同步提及 ' + comp, comp in tech, '技术方案未提及')

    print('[5] 关键设计决策已补充')
    decisions = section(product, '## 7. 关键设计决策')
    check('可定位关键设计决策章节', bool(decisions))
    for mark in ['⑦', '⑧', '⑨']:
        check('决策 ' + mark + ' 已写入', mark in decisions)
    check('决策记录前端轻量化', '轻前端' in decisions or '与原型同构' in decisions)

    print('[6] 待确认事项已更新')
    todo = section(product, '## 11. 待确认事项')
    check('「检索实现深度」已从待确认移出', '检索实现深度' not in todo)
    check('会话持久化仍在待确认', '会话持久化' in todo)
    check('记录本版已定稿的结论', '已定稿' in todo)

    print('[7] 技术方案关键内容')
    for kw in TECH_KEYWORDS:
        check('技术方案含 ' + kw, kw in tech)
    for fn in ['buildAnswer', 'processAudio', 'searchSegments']:
        check('技术方案映射原型函数 ' + fn, fn in tech)

    print('[8] 原型 README 与正式版一致')
    rel = section(readme, '## 与正式版的关系')
    check('可定位「与正式版的关系」章节', bool(rel))
    check('README 不再声明前端为 Streamlit', 'Streamlit' not in rel)
    check('README 声明正式版前端为 FastAPI', 'FastAPI' in rel)
    check('README 声明编排层为 LangGraph', 'LangGraph' in rel)

    print('[9] Markdown 格式')
    p_issues = rule_line_issues(product)
    t_issues = rule_line_issues(tech)
    check('产品文档无「段落紧跟分隔线」', not p_issues, '；'.join(p_issues))
    check('技术方案无「段落紧跟分隔线」', not t_issues, '；'.join(t_issues))

    return list(FAILURES)


def test_docs_consistency() -> None:
    failures = run()
    assert not failures, '文档一致性检查未通过：\n' + '\n'.join(failures)


def main() -> int:
    failures = run()
    print('')
    print('-' * 52)
    if failures:
        print('失败 %d 项 / 通过 %d 项' % (len(failures), COUNT - len(failures)))
        for f in failures:
            print('  · ' + f)
        return 1
    print('全部通过：%d 项检查' % COUNT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
