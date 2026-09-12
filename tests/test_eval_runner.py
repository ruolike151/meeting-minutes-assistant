"""评测运行器（M5 T2 T3 T6 T7）：一次运行产出指标与产物，且离线不碰模型。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.config import Settings
from app.eval.dataset import SECTION_FILES
from app.eval.runner import (
    EvalBudgetExceeded,
    RunConfig,
    estimate_calls,
    prepare_settings,
    run,
    write_artifacts,
)


def write_mini_dataset(base: Path) -> Path:
    """覆盖三场样例会议的最小数据集（每条链路都有样本，但保持测试快）。"""
    base.mkdir(parents=True, exist_ok=True)
    payloads = {
        "retrieval": [
            {"query": "首页入口太深的问题", "gold_segments": ["m_20260911:s2"]},
            {"query": "退款之后积分怎么处理", "gold_segments": ["m_20260909:s2"]},
            {"query": "冷启动中位数是多少秒", "gold_segments": ["m_20260904:s2"]},
            {"query": "公司食堂今天中午吃什么？", "gold_segments": []},
        ],
        "routes": [
            {"query": "官网改版周会定了什么？", "intent": "query_content",
             "answer_mode": "decisions", "scope": ["m_20260911"]},
            {"query": "会员体系二期需求评审的决议是什么", "intent": "query_content",
             "answer_mode": "decisions", "scope": ["m_20260909"]},
            {"query": "移动端体验专项讨论有哪些风险", "intent": "query_content",
             "answer_mode": "risks", "scope": ["m_20260904"]},
        ],
        "qa": [
            {"query": "这次改版的设计稿什么时候交付？", "meeting_id": "m_20260911",
             "gold_points": ["今天中午之前交付全套设计稿"],
             "allowed_segments": ["m_20260911:s3", "m_20260911:s4", "m_20260911:s12"]},
            {"query": "退款之后积分怎么处理？", "meeting_id": "m_20260909",
             "gold_points": ["退款当天扣回对应积分"],
             "allowed_segments": ["m_20260909:s2", "m_20260909:s3"]},
            {"query": "移动端冷启动的实测数据是多少？", "meeting_id": "m_20260904",
             "gold_points": ["冷启动中位数 2.8 秒"],
             "allowed_segments": ["m_20260904:s2", "m_20260904:s3"]},
            {"query": "帮我写一首关于秋天的诗", "must_refuse": True,
             "gold_points": [], "allowed_segments": []},
        ],
        "minutes": [
            {"meeting_id": "m_20260911",
             "action_items": [
                 {"task": "交付首页改版全套设计稿", "owner": "p_li",
                  "due": "2026-09-11", "evidence": ["m_20260911:s3"]}
             ],
             "decisions": ["首页入口层级由三级简化至两级，活动展示区面积扩大一倍。"],
             "risks": []},
            {"meeting_id": "m_20260909",
             "action_items": [
                 {"task": "与财务确认退款后积分已消费的处理方式", "owner": "p_sun",
                  "due": "2026-09-14", "evidence": ["m_20260909:s5"]}
             ],
             "decisions": ["会员等级升级规则采用滚动 12 个月累计。"],
             "risks": []},
            {"meeting_id": "m_20260904",
             "action_items": [
                 {"task": "实现首屏接口并发调用、图片压缩与懒加载", "owner": "p_wang",
                  "due": None, "evidence": ["m_20260904:s5"]}
             ],
             "decisions": [], "risks": []},
        ],
        "review": [
            {"item_id": "m_20260911:a4", "kind": "action_item", "expected_action": "confirm"}
        ],
    }
    for key, filename in SECTION_FILES.items():
        (base / filename).write_text(
            json.dumps(
                {
                    "version": "test-1",
                    "updated_at": "2026-09-12",
                    "source": "sample",
                    "cases": payloads[key],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return base


@pytest.fixture()
def mini_dataset(tmp_path: Path) -> Path:
    return write_mini_dataset(tmp_path / "dataset")


def test_prepare_settings_forces_offline_mode(settings: Settings, tmp_path: Path) -> None:
    """离线运行必须真的关掉模型——本机 .env 里的 Key 不能被悄悄用上。"""
    armed = replace(settings, llm_mode="live", llm_api_key="sk-test-not-real")
    run_settings = prepare_settings(armed, RunConfig(run_type="offline"), tmp_path / "run")
    assert run_settings.llm_mode == "off"
    assert run_settings.effective_llm_mode == "off"
    live_settings = prepare_settings(armed, RunConfig(run_type="live"), tmp_path / "run2")
    assert live_settings.effective_llm_mode == "live"


def test_dry_run_makes_no_calls(settings: Settings, mini_dataset: Path) -> None:
    result = run(settings, RunConfig(run_type="live", dry_run=True), dataset_path=mini_dataset)
    assert result.metrics["dry_run"] is True
    assert result.metrics["estimate"]["total"] > 0
    assert result.details == {}


def test_offline_run_produces_metrics_and_artifacts(
    settings: Settings, mini_dataset: Path
) -> None:
    result = run(
        settings,
        RunConfig(run_type="offline", encoder="hash"),
        dataset_path=mini_dataset,
    )
    metrics = result.metrics
    assert set(metrics) >= {"retrieval", "routes", "qa", "minutes", "review", "cost"}
    assert metrics["retrieval"]["cases"] == 4
    assert metrics["retrieval"]["refuse_accuracy"] == 1.0, "域外问题必须拒答"
    assert metrics["routes"]["cases"] == 3
    # 离线运行不得产生任何模型费用，也不得写 llm_cache
    assert metrics["cost"]["total_tokens"] == 0
    assert metrics["minutes"]["offline_placeholder"] is True

    run_dir = write_artifacts(result, settings.eval_output_dir / result.run_id)
    assert (run_dir / "raw.json").is_file()
    assert (run_dir / "metrics.json").is_file()
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "指标汇总" in report and "局限" in report
    assert "不作为质量结论" in report


def test_run_uses_fresh_data_dir(settings: Settings, mini_dataset: Path) -> None:
    """评测在临时数据目录里跑：不能污染真实 data/。"""
    before = settings.db_path.exists()
    result = run(
        settings, RunConfig(run_type="offline", encoder="hash"), dataset_path=mini_dataset
    )
    assert result.config["run_type"] == "offline"
    assert settings.db_path.exists() is before


def test_budget_exceeded_aborts(settings: Settings, mini_dataset: Path) -> None:
    """预算保护：token 超限必须中止，而不是默默烧完。"""
    # 离线运行 token 为 0，把预算设成负数即可触发同一条分支
    strict = replace(settings, eval_token_budget=-1)
    with pytest.raises(EvalBudgetExceeded):
        run(strict, RunConfig(run_type="offline", encoder="hash"), dataset_path=mini_dataset)


def test_estimate_calls_counts_by_mode() -> None:
    from app.eval.dataset import load_dataset

    root = Path(__file__).resolve().parents[1]
    dataset = load_dataset(root / "samples" / "annotations" / "m5")
    offline = estimate_calls(dataset, RunConfig(run_type="offline"))
    assert offline["total"] == 0, "离线不调模型"
    live = estimate_calls(dataset, RunConfig(run_type="live", judge_mode="llm"))
    assert live["total"] > 0 and live["judge"] > 0
