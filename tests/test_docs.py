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
M2_PLAN = ROOT / 'docs' / 'M2-感知阶段实施计划.md'
M3_PLAN = ROOT / 'docs' / 'M3-编排与检索阶段实施计划.md'
M3_REPORT = ROOT / 'docs' / 'M3-编排与检索评测报告.md'
M4_PLAN = ROOT / 'docs' / 'M4-协同阶段实施计划.md'
M5_PLAN = ROOT / 'docs' / 'M5-评测阶段实施计划.md'
M4_REPORT = ROOT / 'docs' / 'M4-协同阶段验收报告.md'
M5_REPORT = ROOT / 'docs' / 'M5-评测报告.md'
M5_DASHBOARD = ROOT / 'docs' / 'M5-可观测看板.md'
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

M3_PLAN_SECTIONS = [
    '## 1. 目标与验收口径',
    '## 2. 现状盘点',
    '## 3. 差距分析',
    '## 5. 关键设计',
    '## 8. 任务分解',
    '## 9. 测试策略',
    '## 10. 需要你决策的事项',
    '## 11. 需要你提供或确认的东西',
    '## 15. M3 明确不做',
]

# 计划里必须给出的任务编号：T0 到 T9 一个都不能少
M3_PLAN_TASKS = ['### T%d ' % index for index in range(10)]

# M4/M5 是待执行的两份计划：结构、任务编号与关键约束都要写全，
# 否则执行到一半才发现"计划里没提这件事"。
M4_PLAN_SECTIONS = [
    '## 1. 目标与验收口径',
    '## 2. 现状盘点',
    '## 3. 差距分析',
    '## 5. 关键设计',
    '## 8. 任务分解',
    '## 9. 测试策略',
    '## 10. 需要你决策的事项',
    '## 11. 需要你提供或确认的东西',
    '## 15. M4 明确不做',
]

M4_PLAN_TASKS = ['### T%d ' % index for index in range(9)]

M4_PLAN_KEYWORDS = [
    'C3 不回退',
    '事件契约',
    'interrupt',
    'Command(resume',
    'checkpointer',
    'override',
    'review_gate',
    'awaiting_review',
    'MA_REVIEW_MODE',
    'MA_CHECKPOINT_BACKEND',
]

M5_PLAN_SECTIONS = [
    '## 1. 目标与验收口径',
    '## 2. 现状盘点',
    '## 3. 差距分析',
    '## 5. 关键设计',
    '## 8. 任务分解',
    '## 9. 测试策略',
    '## 10. 需要你决策的事项',
    '## 11. 需要你提供或确认的东西',
    '## 15. M5 明确不做',
]

M5_PLAN_TASKS = ['### T%d ' % index for index in range(10)]

M5_PLAN_KEYWORDS = [
    'C3 不回退',
    'Recall@k',
    'MRR',
    '拒答',
    '证据正确率',
    '人工抽样',
    '重排',
    'Langfuse',
    'MA_EVAL_TOKEN_BUDGET',
    'MA_RERANK_MODE',
]

# M5 报告必须交代的：口径、数据缺口、消融、失败归因、局限
M5_REPORT_SECTIONS = [
    '## 1. 评测口径',
    '## 2. 数据集规模与覆盖',
    '## 3. 指标汇总',
    '## 4. 消融矩阵',
    '## 5. 失败明细与归因',
    '## 6. 成本与延迟',
    '## 7. 真实模型运行（待 Key）',
    '## 8. 局限与下一步',
]

M5_MODULE_FILES = [
    'app/eval/dataset.py',
    'app/eval/metrics.py',
    'app/eval/runner.py',
    'app/eval/report.py',
    'app/eval/judge.py',
    'app/retrieval/rerank.py',
    'app/observability/langfuse.py',
    'app/agent/prompts/judge.md',
    'scripts/run_eval_all.py',
    'scripts/eval_minutes.py',
    'scripts/eval_dialogue.py',
    'docker-compose.langfuse.yml',
    'samples/annotations/m5/retrieval_gold.json',
    'samples/annotations/m5/route_gold.json',
    'samples/annotations/m5/qa_gold.json',
    'samples/annotations/m5/minutes_gold.json',
    'samples/annotations/m5/review_gold.json',
]

M5_ENV_KEYS = [
    'MA_EVAL_DATASET_DIR',
    'MA_EVAL_TOKEN_BUDGET',
    'MA_RERANK_MODE',
    'MA_RERANK_MODEL_DIR',
    'MA_RERANK_TOP_N',
    'MA_JUDGE_MODE',
    'MA_LANGFUSE_ENABLED',
    'MA_LANGFUSE_HOST',
]

# M4 验收报告必须覆盖的章节（与 M3 报告同款约束：报告要有口径、结论与偏差）
M4_REPORT_SECTIONS = [
    '## 1. 验收口径与结论',
    '## 2. 挂起与恢复',
    '## 3. 待确认区与人工动作',
    '## 4. 证据回跳与原声',
    '## 5. 状态机与事件',
    '## 6. 测试与回归',
    '## 7. 与计划的显式偏差',
]

M3_REPORT_SECTIONS = [
    '## 1. 评测口径',
    '## 2. 三档编码器的对比',
    '## 3. 阈值取舍',
    '## 4. 编排与工程口径的验证',
    '## 5. 真实 LLM 实测',
]

# 《M3 计划》承诺的模块与环境开关必须在代码里真实存在，
# 否则文档会变成一份"看起来很美但没落地"的清单。
M3_MODULE_FILES = [
    'app/agent/graph.py',
    'app/agent/minutes.py',
    'app/agent/history.py',
    'app/agent/synthesize.py',
    'app/agent/persist.py',
    'app/agent/verify.py',
    'app/agent/nodes/context.py',
    'app/agent/nodes/routing_nodes.py',
    'app/agent/nodes/retrieval_nodes.py',
    'app/agent/nodes/answer_nodes.py',
    'app/agent/prompts/route.md',
    'app/agent/prompts/synthesize.md',
    'app/agent/prompts/minutes_map.md',
    'app/agent/prompts/minutes_reduce.md',
    'app/agent/prompts/verify.md',
    'app/llm/openai_compat.py',
    'app/llm/cache.py',
    'app/llm/structured.py',
    'app/llm/factory.py',
    'app/retrieval/encoder.py',
    'app/retrieval/vector.py',
    'app/retrieval/fusion.py',
    'app/retrieval/timefilter.py',
    'app/retrieval/index.py',
    'app/retrieval/evaluate.py',
    'app/store/embeddings_repo.py',
    'app/store/traces_repo.py',
    'app/store/vector_codec.py',
    'scripts/eval_retrieval.py',
    'scripts/eval_llm_smoke.py',
    'scripts/warm_cache.py',
]

M3_ENV_KEYS = [
    'MA_LLM_PROVIDER',
    'MA_LLM_MODE',
    'MA_LLM_BASE_URL',
    'MA_LLM_API_KEY',
    'MA_LLM_MODEL_ANSWER',
    'MA_LLM_STRUCTURED_MODE',
    'MA_ROUTE_MODE',
    'MA_VERIFY_MODE',
    'MA_TEXT_ENCODER',
    'MA_EMBEDDING_MODEL_DIR',
    'MA_RECALL_K2',
    'MA_RRF_K',
    'MA_MIN_COSINE',
    'MA_HISTORY_ROUNDS',
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
    check('M2 实施计划存在', M2_PLAN.is_file(), str(M2_PLAN))
    check('M3 实施计划存在', M3_PLAN.is_file(), str(M3_PLAN))
    check('M4 实施计划存在', M4_PLAN.is_file(), str(M4_PLAN))
    check('M5 实施计划存在', M5_PLAN.is_file(), str(M5_PLAN))
    check('原型 README 存在', PROTO_README.is_file(), str(PROTO_README))
    if FAILURES:
        return list(FAILURES)

    product = read(PRODUCT_DOC)
    tech = read(TECH_DOC)
    readme = read(PROTO_README)
    m3_plan = read(M3_PLAN)
    m3_report = read(M3_REPORT)
    m4_plan = read(M4_PLAN)
    m5_plan = read(M5_PLAN)
    env_example = read(ROOT / '.env.example')
    config_source = read(ROOT / 'app' / 'config.py')
    readme_root = read(ROOT / 'README.md')
    tech_status = read(TECH_DOC)

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
    m_issues = rule_line_issues(m3_plan)
    check('产品文档无「段落紧跟分隔线」', not p_issues, '；'.join(p_issues))
    check('技术方案无「段落紧跟分隔线」', not t_issues, '；'.join(t_issues))
    check('M3 计划无「段落紧跟分隔线」', not m_issues, '；'.join(m_issues))
    m4_issues = rule_line_issues(m4_plan)
    m5_issues = rule_line_issues(m5_plan)
    check('M4 计划无「段落紧跟分隔线」', not m4_issues, '；'.join(m4_issues))
    check('M5 计划无「段落紧跟分隔线」', not m5_issues, '；'.join(m5_issues))

    print('[10] M3 实施计划结构')
    for heading in M3_PLAN_SECTIONS:
        check('M3 计划含章节 ' + heading, heading in m3_plan)
    missing_tasks = [task for task in M3_PLAN_TASKS if task not in m3_plan]
    check('M3 计划含 T0–T9 全部任务', not missing_tasks, '缺失：' + '、'.join(missing_tasks))
    check('M3 计划声明 C3 不回退', 'C3 不回退' in m3_plan)
    check('M3 计划声明事件契约不变', '事件契约不变' in m3_plan)
    check('M3 计划列出三个必答决策', m3_plan.count('### ①') == 1 and m3_plan.count('### ③') == 1)
    check('M3 计划给出 M4/M5 划界', 'M4 协同' in m3_plan and 'M5 评测' in m3_plan)

    print('[11] M3 计划与实现一致')
    for heading in M3_REPORT_SECTIONS:
        check('M3 评测报告含章节 ' + heading, heading in m3_report)
    check('技术方案标记 M3 已完成', 'M3 编排与检索 ✅' in tech_status)
    missing_files = [path for path in M3_MODULE_FILES if not (ROOT / path).is_file()]
    check('M3 计划提到的模块都已落地', not missing_files, '缺失：' + '、'.join(missing_files))
    missing_env = [key for key in M3_ENV_KEYS if key not in config_source]
    check('M3 开关都能在 config.py 里找到', not missing_env, '缺失：' + '、'.join(missing_env))
    missing_example = [key for key in M3_ENV_KEYS if key not in env_example]
    check('.env.example 覆盖全部 M3 开关', not missing_example, '缺失：' + '、'.join(missing_example))
    check('.env 未入库（.gitignore 已排除）', '.env' in read(ROOT / '.gitignore'))
    check('SSE 事件契约写在 M3 计划里', 'stage' in m3_plan and 'delta' in m3_plan)

    print('[12] M4 协同计划结构')
    for heading in M4_PLAN_SECTIONS:
        check('M4 计划含章节 ' + heading, heading in m4_plan)
    missing_m4_tasks = [task for task in M4_PLAN_TASKS if task not in m4_plan]
    check('M4 计划含 T0–T8 全部任务', not missing_m4_tasks, '缺失：' + '、'.join(missing_m4_tasks))
    for keyword in M4_PLAN_KEYWORDS:
        check('M4 计划含 ' + keyword, keyword in m4_plan, '关键设计未写全')
    check('M4 计划列出待决策事项', m4_plan.count('### ①') == 1 and m4_plan.count('### ④') == 1)
    check('M4 计划给出 M5 划界', 'M5 评测' in m4_plan)

    print('[13] M5 评测计划结构')
    for heading in M5_PLAN_SECTIONS:
        check('M5 计划含章节 ' + heading, heading in m5_plan)
    missing_m5_tasks = [task for task in M5_PLAN_TASKS if task not in m5_plan]
    check('M5 计划含 T0–T9 全部任务', not missing_m5_tasks, '缺失：' + '、'.join(missing_m5_tasks))
    for keyword in M5_PLAN_KEYWORDS:
        check('M5 计划含 ' + keyword, keyword in m5_plan, '关键设计未写全')
    check('M5 计划列出待决策事项', m5_plan.count('### ①') == 1 and m5_plan.count('### ⑤') == 1)
    check('M5 计划收口 M3 的重排决策', 'M3 决策 ③' in m5_plan)
    check('M5 计划声明单测与评测分离', '单测与评测分离' in m5_plan)

    print('[14] README 与两份计划互相指向')
    check('README 链接 M4 计划', 'M4-协同阶段实施计划.md' in readme_root)
    check('README 链接 M5 计划', 'M5-评测阶段实施计划.md' in readme_root)
    check('M4 计划链接 M5 计划', 'M5-评测阶段实施计划.md' in m4_plan)
    check('M5 计划链接 M4 计划', 'M4-协同阶段实施计划.md' in m5_plan)

    print('[15] M4 验收报告与实现一致')
    if not M4_REPORT.is_file():
        check('M4 验收报告存在', False, str(M4_REPORT))
        return list(FAILURES)
    m4_report = read(M4_REPORT)
    for heading in M4_REPORT_SECTIONS:
        check('M4 验收报告含章节 ' + heading, heading in m4_report)
    check('技术方案标记 M4 已完成', 'M4 协同 ✅' in tech_status)
    check('README 链接 M4 验收报告', 'M4-协同阶段验收报告.md' in readme_root)
    check('M4 计划标记实施完成', '已实施完成' in m4_plan)
    for path in (
        'app/agent/checkpoint.py',
        'app/agent/review.py',
        'app/agent/nodes/minutes_nodes.py',
        'app/store/review_repo.py',
    ):
        check('M4 新模块已落地 ' + path, (ROOT / path).is_file())
    for key in ('MA_CHECKPOINT_BACKEND', 'MA_REVIEW_MODE', 'MA_REVIEW_CONFIDENCE'):
        check('M4 开关写在 config.py ' + key, key in config_source)
        check('M4 开关写在 .env.example ' + key, key in env_example)

    print('[16] M5 评测报告与实现一致')
    if not M5_REPORT.is_file():
        check('M5 评测报告存在', False, str(M5_REPORT))
        return list(FAILURES)
    m5_report = read(M5_REPORT)
    for heading in M5_REPORT_SECTIONS:
        check('M5 报告含章节 ' + heading, heading in m5_report)
    check('技术方案标记 M5 已完成', 'M5 评测 ✅' in tech_status)
    check('README 链接 M5 评测报告', 'M5-评测报告.md' in readme_root)
    check('M5 计划标记实施完成', '已实施完成' in m5_plan)
    check('可观测看板文档存在', M5_DASHBOARD.is_file())
    missing_modules = [path for path in M5_MODULE_FILES if not (ROOT / path).is_file()]
    check('M5 计划提到的模块都已落地', not missing_modules,
          '缺失：' + '、'.join(missing_modules))
    missing_env = [key for key in M5_ENV_KEYS if key not in config_source]
    check('M5 开关都能在 config.py 里找到', not missing_env, '缺失：' + '、'.join(missing_env))
    missing_example = [key for key in M5_ENV_KEYS if key not in env_example]
    check('.env.example 覆盖全部 M5 开关', not missing_example,
          '缺失：' + '、'.join(missing_example))
    check('M5 报告标注数据来源缺口',
          '真实会议录音' in m5_report and '待用户提供' in m5_report)
    check('M5 报告给出重排结论', '重排结论' in m5_report)

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
