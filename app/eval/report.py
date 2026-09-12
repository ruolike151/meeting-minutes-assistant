"""报告渲染（M5 T9）：把一次或多次运行渲染成 Markdown。

报告结构固定（《M5 计划》5.8 节），并且**必须**包含"局限"一节——
样本量、金标来源、哪些指标靠规则近似，都写在报告里，
避免读者把"跑出数字"误当成"效果已被证明"。
"""

from __future__ import annotations

def _fmt(value, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return ("%." + str(digits) + "f") % value
    return str(value)


def summary_table(results: list) -> str:
    """消融矩阵：每个配置一行，列是指标。"""
    header = (
        "| 配置 | 编码器 | Recall@8 | MRR | 跨会议准确率 | 域外拒答 | 域内作答率 | "
        "路由准确率 | 证据正确率 | 要点覆盖 | 待办 F1 | 检索 P95(ms) |\n"
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |\n"
    )
    rows = []
    for result in results:
        metrics = result.metrics
        retrieval = metrics.get("retrieval", {})
        routes = metrics.get("routes", {})
        qa = metrics.get("qa", {})
        minutes = metrics.get("minutes", {})
        rows.append(
            "| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
            % (
                result.config.get("label") or result.config.get("run_type", ""),
                result.config.get("encoder_resolved", result.config.get("encoder", "")),
                _fmt(retrieval.get("recall_at_k"), 3),
                _fmt(retrieval.get("mrr"), 3),
                _fmt(retrieval.get("cross_meeting_accuracy"), 3),
                _fmt(retrieval.get("refuse_accuracy"), 3),
                _fmt(retrieval.get("answer_rate"), 3),
                _fmt(routes.get("routing_accuracy"), 3),
                _fmt(qa.get("evidence_correctness"), 3),
                _fmt(qa.get("point_coverage"), 3),
                _fmt(minutes.get("action_f1"), 3),
                _fmt(retrieval.get("p95_query_ms"), 1),
            )
        )
    return header + "\n".join(rows) + "\n"


def render_report(result, *, history: list | None = None) -> str:
    """渲染单次运行报告；`history` 给出时附上消融对比表。"""
    metrics = result.metrics
    retrieval = metrics.get("retrieval", {})
    routes = metrics.get("routes", {})
    qa = metrics.get("qa", {})
    minutes = metrics.get("minutes", {})
    review = metrics.get("review", {})
    cost = metrics.get("cost", {})
    dataset = result.dataset

    lines: list[str] = []
    lines.append("# M5 评测报告（运行 %s）" % result.run_id)
    lines.append("")
    lines.append(
        "生成时间：%s　运行类型：%s　数据集版本：%s（来源：%s）"
        % (
            result.created_at,
            result.config.get("run_type", ""),
            dataset.get("version", ""),
            dataset.get("source", ""),
        )
    )
    lines.append("")
    lines.append("## 1. 评测口径")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("| --- | --- |")
    for key, value in result.config.items():
        lines.append("| %s | %s |" % (key, value))
    lines.append("| 数据集规模 | %s |" % dataset.get("size", {}))
    lines.append("| 耗时 | %s s |" % metrics.get("elapsed_seconds", "—"))
    lines.append("")
    lines.append("## 2. 数据集规模与覆盖")
    lines.append("")
    lines.append("| 分项 | 用例数 |")
    lines.append("| --- | ---: |")
    for key, value in (dataset.get("size") or {}).items():
        lines.append("| %s | %s |" % (key, value))
    lines.append("")
    lines.append("## 3. 指标汇总")
    lines.append("")
    rerank_modes = {item.metrics.get("retrieval", {}).get("rerank_mode") for item in (history or [result])}
    if rerank_modes - {None, "off"}:
        shown = result
        for item in history or []:
            if item.metrics.get("retrieval", {}).get("rerank_mode") not in (None, "off"):
                shown = item
                break
        rerank_metrics = shown.metrics.get("retrieval", {})
        lines.append(
            "重排：%s，生效 %s / %s 条查询%s"
            % (
                rerank_metrics.get("rerank_mode"),
                rerank_metrics.get("reranked_cases"),
                rerank_metrics.get("cases"),
                "；原因：%s" % rerank_metrics["rerank_reason"]
                if rerank_metrics.get("rerank_reason")
                else "",
            )
        )
        lines.append("")
    lines.append("| 维度 | 指标 | 值 |")
    lines.append("| --- | --- | ---: |")
    rows = [
        ("纪要质量", "Schema 合法率", minutes.get("schema_valid_rate")),
        ("纪要质量", "待办抽取准确率", minutes.get("action_precision")),
        ("纪要质量", "待办抽取召回率", minutes.get("action_recall")),
        ("纪要质量", "待办抽取 F1", minutes.get("action_f1")),
        ("纪要质量", "负责人归属准确率", minutes.get("owner_accuracy")),
        ("纪要质量", "关键结论命中率", minutes.get("decision_hit_rate")),
        ("纪要质量", "风险命中率", minutes.get("risk_hit_rate")),
        ("证据可靠性", "证据正确率（规则版）", qa.get("evidence_correctness")),
        ("证据可靠性", "引用越界率", 1 - (qa.get("citation_scope_accuracy") or 0)),
        ("证据可靠性", "要点覆盖率", qa.get("point_coverage")),
        ("检索效果", "Recall@8", retrieval.get("recall_at_k")),
        ("检索效果", "MRR", retrieval.get("mrr")),
        ("检索效果", "跨会议检索准确率", retrieval.get("cross_meeting_accuracy")),
        ("对话行为", "意图路由准确率", routes.get("routing_accuracy")),
        ("对话行为", "多轮上下文保持正确率", routes.get("multi_turn_accuracy")),
        ("对话行为", "域外拒答正确率", retrieval.get("refuse_accuracy")),
        ("对话行为", "域内作答率", retrieval.get("answer_rate")),
        ("协同", "待确认检出率", review.get("detection_rate")),
        ("协同", "待确认精度", review.get("precision")),
        ("成本", "单次问答 token", _per_round(cost)),
        ("成本", "单次问答费用", _per_round(cost, key="cost")),
        ("成本", "P95 延迟（ms）", cost.get("p95_latency_ms")),
        ("成本", "缓存命中率", cost.get("cache_hit_rate")),
    ]
    for dimension, name, value in rows:
        lines.append("| %s | %s | %s |" % (dimension, name, _fmt(value, 3)))
    if minutes.get("offline_placeholder"):
        lines.append("")
        lines.append(
            "> 注：本次是离线运行，模型关闭，纪要内容来自固定备用纪要。"
            "上表中与纪要相关的四个指标只证明匹配逻辑与接线正确，"
            "**不作为质量结论**；质量数字需要 `--live` 运行。"
        )
    lines.append("")
    if history:
        lines.append("## 4. 消融矩阵")
        lines.append("")
        lines.append(summary_table(history))
    else:
        lines.append("## 4. 消融矩阵")
        lines.append("")
        lines.append("本次只跑了一个配置；`scripts/run_eval_all.py` 会跑多组并在此对比。")
        lines.append("")
    lines.append("## 5. 分项明细")
    lines.append("")
    lines.append("逐条明细见同目录 `raw.json`（检索命中、引用判定、路由对照、纪要匹配）。")
    lines.append("")
    lines.append("## 6. 成本与延迟")
    lines.append("")
    lines.append(
        "问答轮数 %s，输入 %s token，输出 %s token，费用 %s（价格口径见 `.env.example`）。"
        % (
            cost.get("rounds", 0),
            cost.get("input_tokens", 0),
            cost.get("output_tokens", 0),
            cost.get("cost", 0.0),
        )
    )
    lines.append("")
    lines.append("## 7. 局限与下一步")
    lines.append("")
    for item in limitations(result):
        lines.append("- " + item)
    lines.append("")
    return "\n".join(lines)


def _per_round(cost: dict, *, key: str = "total_tokens") -> float:
    rounds = cost.get("rounds") or 0
    if not rounds:
        return 0.0
    return round((cost.get(key) or 0) / rounds, 4)


def limitations(result) -> list[str]:
    """把"这次跑的是什么、不能从数字里推出什么"写清楚。"""
    items: list[str] = []
    config = result.config
    dataset = result.dataset
    if config.get("run_type") == "offline":
        items.append(
            "本次是离线基线（未调用真实模型）：纪要指标衡量的是链路接线与匹配逻辑，"
            "不代表模型质量；真实质量需要 `--live`（需要 Key）。"
        )
    if dataset.get("source") == "sample":
        items.append(
            "数据集来源是仓库内的样例会议（3 场 / 32 片段）。真实会议录音（脱敏）"
            "由用户提供后按同一 schema 补入，届时本报告需重跑。"
        )
    if (dataset.get("size") or {}).get("minutes", 0) < 5:
        items.append(
            "纪要金标会议数少于决策 ④ 的目标（5 场），统计置信度有限。"
        )
    if config.get("judge_mode") == "rule":
        items.append(
            "证据正确率由规则版给出（引用存在 + 关键词覆盖率），"
            "存在假阳性；`MA_JUDGE_MODE=llm` 可跑 LLM 判定版，报告应两者并列。"
        )
    items.append(
        "P95 延迟基于离线运行，不含真实模型耗时；联机数字在 `--live` 运行里给出。"
    )
    return items


__all__ = ["limitations", "render_report", "summary_table"]
