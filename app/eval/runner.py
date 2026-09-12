"""评测运行器（M5 T2 T3 T4 T6 T7）。

一次运行 = 一个全新的数据目录 + 一份数据集 + 一组配置，产出：

* `raw.json`：逐条明细（每条查询的命中、引用、判定），便于定位而不是只看总分；
* `metrics.json`：汇总指标；
* `report.md`：人看的报告（由 `report.py` 渲染）。

**离线可跑**是硬要求：默认 `MA_LLM_MODE=off`，模型与向量都走确定性兜底，
因此任何人都能重跑出同样的数字（约束 C3、M5 Global Constraints 第 1 条）。
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from app.agent.minutes import generate_meeting_minutes
from app.agent.pipeline import plain_text, respond
from app.config import Settings
from app.eval.dataset import EvalDataset, check_against_db, load_dataset
from app.eval.judge import rule_point_coverage, rule_support, sample_for_review
from app.eval.metrics import (
    RefusalOutcome,
    accuracy,
    aggregate_cost,
    confusion_matrix,
    mrr,
    percentile,
    precision_recall_f1,
    recall_at_k,
    refusal_metrics,
)
from app.retrieval.encoder import EncoderUnavailable, select_encoder
from app.retrieval.pipeline import retrieve
from app.retrieval.rerank import build_reranker
from app.store.bootstrap import bootstrap
from app.store.minutes_repo import MinutesRepo, pending_items
from app.store.repos import MeetingRepo, SegmentRepo, new_id
from app.store.traces_repo import TraceRepo

TASK_SIMILARITY_THRESHOLD = 0.75


class EvalBudgetExceeded(RuntimeError):
    """真实模型评测超出 token 预算（M5 Global Constraints 第 6 条）。"""


@dataclass
class RunConfig:
    run_type: str = "offline"       # offline | cache_first | live
    encoder: str = "auto"           # auto | hash | keyword
    rerank: str = "off"             # off | cross_encoder
    review_mode: str = "off"        # 评测默认不挂起（挂起会阻塞批量问答）
    judge_mode: str = "rule"        # rule | llm
    top_k: int = 8
    dry_run: bool = False
    label: str = ""                 # 报告里的配置名（消融对比用）

    def snapshot(self) -> dict:
        return {
            "run_type": self.run_type,
            "encoder": self.encoder,
            "rerank": self.rerank,
            "review_mode": self.review_mode,
            "judge_mode": self.judge_mode,
            "top_k": self.top_k,
            "label": self.label or self.run_type,
        }


@dataclass
class RunResult:
    run_id: str
    created_at: str
    config: dict
    dataset: dict
    metrics: dict
    details: dict = field(default_factory=dict)

    def to_payload(self) -> dict:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "config": self.config,
            "dataset": self.dataset,
            "metrics": self.metrics,
            "details": self.details,
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def prepare_settings(settings: Settings, config: RunConfig, run_dir: Path) -> Settings:
    """评测运行用的设置：全新数据目录 + 运行配置覆盖。

    `--offline` 必须**强制**关掉模型：本机 `.env` 里常常配着真实 Key，
    只按 run_type 打标签而不改模式的话，"离线基线"会悄悄打真实接口——
    数字不可复现、还烧钱（写成用例：`test_offline_run_never_calls_llm`）。
    """
    run_settings = settings.for_data_dir(run_dir / "data")
    llm_mode = {"offline": "off", "cache_first": "cache_first", "live": "live"}.get(
        config.run_type, "off"
    )
    return replace(
        run_settings,
        llm_mode=llm_mode,
        review_mode=config.review_mode,
        rerank_mode=config.rerank,
        judge_mode=config.judge_mode,
        seed_on_start=True,
    )


def build_encoder(run_settings: Settings, config: RunConfig):
    """按配置选编码器：`keyword` 表示不走向量（M1 基线）。"""
    if config.encoder == "keyword":
        return None, "keyword-only"
    if config.encoder == "hash":
        run_settings = replace(run_settings, text_encoder="hash")
    try:
        selection = select_encoder(run_settings)
        return selection.encoder, selection.encoder.storage_key
    except EncoderUnavailable:
        return None, "keyword-only"


def estimate_calls(dataset: EvalDataset, config: RunConfig) -> dict:
    """粗估这次运行会产生多少次模型调用（`--dry-run` 用）。

    只数"一定会调模型"的部分：纪要生成（每场会议按分段数估 3 次）；
    QA / 证据判定只在 `judge_mode=llm` 时按引用数估。缓存命中不计。
    """
    minutes_calls = 0 if config.run_type == "offline" else len(dataset.minutes) * 3
    judge_calls = 0
    if config.judge_mode == "llm" and config.run_type != "offline":
        judge_calls = len(dataset.qa) * 2
    route_calls = 0 if config.run_type == "offline" else len(dataset.routes)
    total = minutes_calls + judge_calls + route_calls
    return {
        "minutes": minutes_calls,
        "judge": judge_calls,
        "route": route_calls,
        "total": total,
        "estimated_tokens": total * 1200,
    }


def run(
    settings: Settings,
    config: RunConfig,
    *,
    dataset_path: Path | None = None,
    run_dir: Path | None = None,
) -> RunResult:
    """跑一次完整评测。`run_dir` 为空时自动建目录（不覆盖已有运行）。"""
    run_id = new_id("run")
    output_root = Path(settings.eval_output_dir)
    run_dir = Path(run_dir) if run_dir is not None else output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset(dataset_path or settings.eval_dataset_dir)

    if config.dry_run:
        return RunResult(
            run_id=run_id,
            created_at=_now(),
            config=config.snapshot(),
            dataset={
                "version": dataset.version,
                "source": dataset.source,
                "size": dataset.size(),
            },
            metrics={"dry_run": True, "estimate": estimate_calls(dataset, config)},
            details={},
        )

    run_settings = prepare_settings(settings, config, run_dir)
    conn = bootstrap(run_settings)
    try:
        problems = check_against_db(dataset, conn)
        if problems:
            raise ValueError("数据集与库内数据不一致：\n" + "\n".join(problems))
        encoder, encoder_name = build_encoder(run_settings, config)
        started = time.perf_counter()
        retrieval_metrics, retrieval_details = evaluate_retrieval(
            conn, run_settings, dataset, encoder=encoder, top_k=config.top_k
        )
        route_metrics, route_details = evaluate_routes(conn, run_settings, dataset)
        qa_metrics, qa_details = evaluate_qa(
            conn, run_settings, dataset, judge_mode=config.judge_mode
        )
        minutes_metrics, minutes_details = evaluate_minutes(
            conn, run_settings, dataset, offline=config.run_type == "offline"
        )
        review_metrics, review_details = evaluate_review(conn, dataset)
        traces = TraceRepo(conn).list_recent(limit=1000)
        cost = aggregate_cost(traces)
        if cost["total_tokens"] > settings.eval_token_budget:
            raise EvalBudgetExceeded(
                "本次运行 token 超出预算：%d > %d"
                % (cost["total_tokens"], settings.eval_token_budget)
            )
        metrics = {
            "retrieval": retrieval_metrics,
            "routes": route_metrics,
            "qa": qa_metrics,
            "minutes": minutes_metrics,
            "review": review_metrics,
            "cost": cost,
            "elapsed_seconds": round(time.perf_counter() - started, 2),
        }
        return RunResult(
            run_id=run_id,
            created_at=_now(),
            config={**config.snapshot(), "encoder_resolved": encoder_name},
            dataset={
                "version": dataset.version,
                "source": dataset.source,
                "size": dataset.size(),
            },
            metrics=metrics,
            details={
                "retrieval": retrieval_details,
                "routes": route_details,
                "qa": qa_details,
                "minutes": minutes_details,
                "review": review_details,
            },
        )
    finally:
        conn.close()


# -- 分项评测 -----------------------------------------------------------


def evaluate_retrieval(
    conn: sqlite3.Connection,
    settings: Settings,
    dataset: EvalDataset,
    *,
    encoder,
    top_k: int = 8,
) -> tuple[dict, list[dict]]:
    """检索：Recall@k、MRR、跨会议准确率、拒答两方向。"""
    outcomes = []
    ranks: list[int | None] = []
    cross_cases = 0
    cross_ok = 0
    reranked_cases = 0
    rerank_reason = ""
    query_ms: list[float] = []
    reranker = build_reranker(settings)
    for case in dataset.retrieval:
        started = time.perf_counter()
        result = retrieve(
            conn,
            case.query,
            encoder=encoder,
            config=settings.retrieval,
            reranker=reranker,
        )
        query_ms.append((time.perf_counter() - started) * 1000)
        ranked = ["%s:%s" % key for key in result.ranked_keys[:top_k]]
        gold = set(case.gold_segments)
        rank = next((index + 1 for index, key in enumerate(ranked) if key in gold), None)
        if gold:
            ranks.append(rank)
        cross_pass = None
        if case.gold_meetings:
            cross_cases += 1
            allowed = set(case.gold_meetings)
            returned = {key.split(":")[0] for key in ranked}
            cross_pass = bool(returned) and returned <= allowed
            cross_ok += 1 if cross_pass else 0
        if result.reranked:
            reranked_cases += 1
        if result.rerank_reason and not rerank_reason:
            rerank_reason = result.rerank_reason
        outcomes.append(
            {
                "query": case.query,
                "answerable": bool(gold),
                "refused": bool(result.refused),
                "rank": rank,
                "recalled": recall_at_k(rank, top_k) == 1.0,
                "matched": ranked,
                "gold": sorted(gold),
                "cross_meeting_pass": cross_pass,
            }
        )
    refusal = refusal_metrics(
        [
            RefusalOutcome(answerable=item["answerable"], refused=item["refused"])
            for item in outcomes
        ]
    )
    metrics = {
        "cases": len(outcomes),
        "answerable": len(ranks),
        "recall_at_k": round(sum(recall_at_k(rank, top_k) for rank in ranks) / len(ranks), 4)
        if ranks
        else 0.0,
        "mrr": mrr(ranks),
        "cross_meeting_accuracy": round(cross_ok / cross_cases, 4) if cross_cases else 0.0,
        "cross_meeting_cases": cross_cases,
        # 重排的真实生效情况：没生效（权重缺失 / 未开启）时要能一眼看出来，
        # 否则消融表会出现"两行一模一样却没人发现"（这个坑踩过一次）
        "rerank_mode": getattr(reranker, "name", "off"),
        "reranked_cases": reranked_cases,
        "rerank_reason": rerank_reason,
        # 重排的代价：检索耗时（含编码与重排的端到端时间，单查询粒度）
        "mean_query_ms": round(sum(query_ms) / len(query_ms), 2) if query_ms else 0.0,
        "p95_query_ms": percentile(query_ms, 0.95),
        **refusal,
    }
    return metrics, outcomes


def evaluate_routes(
    conn: sqlite3.Connection, settings: Settings, dataset: EvalDataset
) -> tuple[dict, list[dict]]:
    """对话行为：意图 / 模式 / 范围三类标签的准确率，含多轮范围继承。"""
    intent_pairs: list[tuple[str, str]] = []
    mode_pairs: list[tuple[str, str]] = []
    scope_pairs: list[tuple[str, str]] = []
    details = []
    for case in dataset.routes:
        conversation_id = None
        for context_query in case.context:
            warmup = respond(
                conn,
                context_query,
                conversation_id=conversation_id,
                active_meeting_id=case.meeting_id,
                settings=settings,
            )
            conversation_id = warmup.conversation_id
        result = respond(
            conn,
            case.query,
            conversation_id=conversation_id,
            active_meeting_id=case.meeting_id,
            settings=settings,
        )
        answer = result.answer
        expected_scope = ",".join(sorted(case.scope))
        actual_scope = ",".join(sorted(answer.scope_meeting_ids))
        intent_pairs.append((case.intent, answer.intent))
        mode_pairs.append((case.answer_mode, answer.answer_mode))
        scope_pairs.append((expected_scope, actual_scope))
        details.append(
            {
                "query": case.query,
                "expected": {
                    "intent": case.intent,
                    "answer_mode": case.answer_mode,
                    "scope": sorted(case.scope),
                },
                "actual": {
                    "intent": answer.intent,
                    "answer_mode": answer.answer_mode,
                    "scope": sorted(answer.scope_meeting_ids),
                },
                "multi_turn": bool(case.context),
                "passed": case.intent == answer.intent
                and case.answer_mode == answer.answer_mode
                and expected_scope == actual_scope,
            }
        )
    metrics = {
        "cases": len(details),
        "intent_accuracy": accuracy(intent_pairs),
        "answer_mode_accuracy": accuracy(mode_pairs),
        "scope_accuracy": accuracy(scope_pairs),
        "routing_accuracy": round(
            sum(1 for item in details if item["passed"]) / len(details), 4
        )
        if details
        else 0.0,
        "multi_turn_cases": sum(1 for item in details if item["multi_turn"]),
        "multi_turn_accuracy": round(
            sum(1 for item in details if item["multi_turn"] and item["passed"])
            / max(1, sum(1 for item in details if item["multi_turn"])),
            4,
        ),
        "confusion": confusion_matrix(
            [item["expected"]["answer_mode"] for item in details],
            [item["actual"]["answer_mode"] for item in details],
        ),
    }
    return metrics, details


def evaluate_qa(
    conn: sqlite3.Connection,
    settings: Settings,
    dataset: EvalDataset,
    *,
    judge_mode: str = "rule",
) -> tuple[dict, list[dict]]:
    """问答：作答 / 拒答、引用是否越界、引用是否支撑、要点覆盖。"""
    details = []
    refusals: list[RefusalOutcome] = []
    citations_total = 0
    citations_supported = 0
    citations_in_scope = 0
    points_hit = 0
    points_total = 0
    review_pool: list[dict] = []
    for case in dataset.qa:
        result = respond(
            conn, case.query, active_meeting_id=case.meeting_id, settings=settings
        )
        answer = result.answer
        refused = answer.answer_mode == "refuse"
        refusals.append(RefusalOutcome(answerable=not case.must_refuse, refused=refused))
        answer_text = plain_text(answer)
        allowed = set(case.allowed_segments)
        allowed_meetings = {key.split(":")[0] for key in allowed}
        if case.meeting_id:
            allowed_meetings.add(case.meeting_id)
        supported_flags: list[bool] = []
        for citation in answer.citations:
            key = citation.key
            citations_total += 1
            # 越界判据放在会议粒度：相邻片段（±1 窗口）是检索层刻意拼进来的，
            # 用"片段必须在金标列表里"去卡会把正确的邻接引用误判成错误。
            in_scope = (not allowed_meetings) or key.split(":")[0] in allowed_meetings
            citations_in_scope += 1 if in_scope else 0
            segment = _segment_text(conn, key)
            supported, coverage = rule_support(
                _item_text(answer, citation.segment_id), segment
            )
            supported_flags.append(supported)
            citations_supported += 1 if supported else 0
            review_pool.append(
                {
                    "query": case.query,
                    "citation": key,
                    "supported": supported,
                    "coverage": coverage,
                }
            )
        hit, total = rule_point_coverage(answer_text, case.gold_points)
        points_hit += hit
        points_total += total
        details.append(
            {
                "query": case.query,
                "must_refuse": case.must_refuse,
                "refused": refused,
                "answer_mode": answer.answer_mode,
                "citations": [citation.key for citation in answer.citations],
                "citations_in_scope": all(
                    (not allowed_meetings) or citation.meeting_id in allowed_meetings
                    for citation in answer.citations
                ),
                "points_hit": hit,
                "points_total": total,
                "answer_text": answer_text[:400],
            }
        )
    sampled = sample_for_review([item["supported"] for item in review_pool])
    metrics = {
        "cases": len(details),
        **refusal_metrics(refusals),
        "evidence_correctness": round(citations_supported / citations_total, 4)
        if citations_total
        else 0.0,
        # 会议粒度：引用有没有跑到不该出现的会议里（跨会议问答的关键指标）
        "citation_scope_accuracy": round(citations_in_scope / citations_total, 4)
        if citations_total
        else 0.0,
        "point_coverage": round(points_hit / points_total, 4) if points_total else 0.0,
        "citations": citations_total,
        "judge_mode": judge_mode,
        "review_sample_size": len(sampled),
        "review_sample": [review_pool[index] for index in sampled],
    }
    return metrics, details


def evaluate_minutes(
    conn: sqlite3.Connection,
    settings: Settings,
    dataset: EvalDataset,
    *,
    offline: bool = False,
) -> tuple[dict, list[dict]]:
    """纪要：Schema 合法率、待办 P/R/F1、负责人归属、关键结论与风险命中。

    `offline=True` 时模型关闭，纪要回落到固定备用内容：这时指标只证明
    "匹配逻辑与接线是对的"，**不是**质量结论（报告必须标明）。
    """
    details = []
    matched_total = 0
    predicted_total = 0
    gold_total = 0
    owner_right = 0
    owner_total = 0
    schema_ok = 0
    decision_hit = 0
    decision_total = 0
    risk_hit = 0
    risk_total = 0
    for gold in dataset.minutes:
        meeting = MeetingRepo(conn).get(gold.meeting_id)
        segments = SegmentRepo(conn).list_for_meeting(gold.meeting_id)
        fallback = MinutesRepo(conn).get(gold.meeting_id)
        try:
            minutes, notes = generate_meeting_minutes(
                settings, conn, meeting, segments, fallback=fallback
            )
            schema_ok += 1
        except Exception as error:  # noqa: BLE001 - 单场失败不该终止整轮评测
            details.append({"meeting_id": gold.meeting_id, "error": str(error)})
            continue
        matched, pairs = _match_actions(minutes.action_items, gold.action_items)
        matched_total += matched
        predicted_total += len(minutes.action_items)
        gold_total += len(gold.action_items)
        for system_item, gold_item in pairs:
            if gold_item.owner:
                owner_total += 1
                owner_right += 1 if system_item.owner == gold_item.owner else 0
        hits, total = _match_texts(
            [decision.content for decision in minutes.decisions], gold.decisions, 0.5
        )
        decision_hit += hits
        decision_total += total
        risk_hits, risk_tot = _match_texts(
            [risk.content for risk in minutes.risks], gold.risks, 0.5
        )
        risk_hit += risk_hits
        risk_total += risk_tot
        details.append(
            {
                "meeting_id": gold.meeting_id,
                "action_items": {
                    "predicted": len(minutes.action_items),
                    "gold": len(gold.action_items),
                    "matched": matched,
                },
                "decisions": {"hit": hits, "gold": total},
                "risks": {"hit": risk_hits, "gold": risk_tot},
                "notes": notes,
            }
        )
    prf = precision_recall_f1(matched_total, predicted_total, gold_total)
    metrics = {
        "meetings": len(dataset.minutes),
        "offline_placeholder": offline,
        "schema_valid_rate": round(schema_ok / len(dataset.minutes), 4)
        if dataset.minutes
        else 0.0,
        "action_precision": prf["precision"],
        "action_recall": prf["recall"],
        "action_f1": prf["f1"],
        "owner_accuracy": round(owner_right / owner_total, 4) if owner_total else 0.0,
        "decision_hit_rate": round(decision_hit / decision_total, 4)
        if decision_total
        else 0.0,
        "risk_hit_rate": round(risk_hit / risk_total, 4) if risk_total else 0.0,
    }
    return metrics, details


def evaluate_review(
    conn: sqlite3.Connection, dataset: EvalDataset
) -> tuple[dict, list[dict]]:
    """协同：该进待确认区的条目有没有被检出来（检出率 / 精度）。"""
    details = []
    detected = 0
    flagged_total = 0
    gold_ids = {case.item_id for case in dataset.review}
    for meeting_id in {case.item_id.split(":")[0] for case in dataset.review}:
        entries = pending_items(conn, meeting_id)
        flagged_total += len(entries)
        for case in dataset.review:
            if not case.item_id.startswith(meeting_id + ":"):
                continue
            _, local_id = case.item_id.split(":", 1)
            found = next((item for item in entries if item.id == local_id), None)
            detected += 1 if found else 0
            details.append(
                {
                    "item_id": case.item_id,
                    "expected_action": case.expected_action,
                    "detected": found is not None,
                    "kind": found.kind if found else None,
                    "reason": found.reason if found else None,
                }
            )
    metrics = {
        "gold_items": len(dataset.review),
        "detection_rate": round(detected / len(dataset.review), 4)
        if dataset.review
        else 0.0,
        "flagged_items": flagged_total,
        # 精度 = 金标覆盖到的待确认条目 / 系统实际挂起的所有条目
        "precision": round(detected / flagged_total, 4) if flagged_total else 0.0,
    }
    return metrics, details


# -- 小工具 -------------------------------------------------------------


def _segment_text(conn: sqlite3.Connection, key: str) -> str:
    try:
        meeting_id, segment_id = key.split(":", 1)
    except ValueError:
        return ""
    row = conn.execute(
        "SELECT text FROM segments WHERE meeting_id = ? AND id = ?",
        (meeting_id, segment_id),
    ).fetchone()
    return row["text"] if row else ""


def _item_text(answer, segment_id: str) -> str:
    """取引用所属结论的文本：优先用该条结论，取不到就退化成整段回答。"""
    for block in answer.blocks:
        for item in block.items:
            if any(citation.segment_id == segment_id for citation in item.citations):
                return " ".join(part for part in (item.title, item.text) if part)
    return plain_text(answer)


def _similarity(first: str, second: str) -> float:
    return SequenceMatcher(None, (first or "").strip(), (second or "").strip()).ratio()


def _match_actions(system_items, gold_items, threshold: float = TASK_SIMILARITY_THRESHOLD):
    """贪心一对一匹配系统待办与金标待办，返回 (匹配数, 配对列表)。"""
    candidates = []
    for system_index, system_item in enumerate(system_items):
        for gold_index, gold_item in enumerate(gold_items):
            score = _similarity(system_item.task, gold_item.task)
            if gold_item.owner and system_item.owner:
                score = score if system_item.owner == gold_item.owner else score - 0.2
            candidates.append((score, system_index, gold_index))
    candidates.sort(reverse=True)
    used_system: set[int] = set()
    used_gold: set[int] = set()
    pairs = []
    for score, system_index, gold_index in candidates:
        if score < threshold:
            break
        if system_index in used_system or gold_index in used_gold:
            continue
        used_system.add(system_index)
        used_gold.add(gold_index)
        pairs.append((system_items[system_index], gold_items[gold_index]))
    return len(pairs), pairs


def _match_texts(system_texts: list[str], gold_texts: list[str], threshold: float):
    """文本级一对一匹配（决议 / 风险用），返回 (命中数, 金标数)。"""
    used: set[int] = set()
    hit = 0
    for gold in gold_texts:
        best_index, best_score = None, 0.0
        for index, text in enumerate(system_texts):
            if index in used:
                continue
            score = _similarity(text, gold)
            if score > best_score:
                best_index, best_score = index, score
        if best_index is not None and best_score >= threshold:
            used.add(best_index)
            hit += 1
    return hit, len(gold_texts)


def write_artifacts(result: RunResult, output_dir: Path | None = None) -> Path:
    """把一次运行的三份产物写盘，返回运行目录。"""
    from app.eval.report import render_report

    run_dir = Path(output_dir) if output_dir is not None else Path(".") / result.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = result.to_payload()
    (run_dir / "raw.json").write_text(
        json.dumps(payload["details"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "metrics.json").write_text(
        json.dumps(
            {
                "run_id": result.run_id,
                "created_at": result.created_at,
                "config": result.config,
                "dataset": result.dataset,
                "metrics": result.metrics,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "report.md").write_text(render_report(result), encoding="utf-8")
    return run_dir


__all__ = [
    "EvalBudgetExceeded",
    "RunConfig",
    "RunResult",
    "build_encoder",
    "estimate_calls",
    "evaluate_minutes",
    "evaluate_qa",
    "evaluate_retrieval",
    "evaluate_review",
    "evaluate_routes",
    "prepare_settings",
    "run",
    "write_artifacts",
]
