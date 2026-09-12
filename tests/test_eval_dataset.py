"""数据集契约（M5 T0）：schema、金标对账与覆盖度。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.eval.dataset import SECTION_FILES, check_against_db, load_dataset
from app.eval.fixtures import ingest_real_meetings

ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "samples" / "annotations" / "m5"


def test_dataset_loads_and_declares_version() -> None:
    dataset = load_dataset(DATASET_DIR)
    assert dataset.version.startswith("m5-")
    assert dataset.updated_at
    sizes = dataset.size()
    assert sizes["retrieval"] >= 50, "检索标注要达到 M5 决策 ④ 的规模"
    assert sizes["routes"] >= 30
    assert sizes["qa"] >= 20
    assert sizes["minutes"] >= 3
    assert sizes["review"] >= 4
    assert sizes["real_meetings"] >= 2, "真实会议语料要在数据集里"
    assert sizes["real_segments"] >= 30


def test_dataset_matches_seed_database(conn) -> None:
    """金标片段、会议都必须真实存在——写错 ID 要当场报错，而不是静默低分。"""
    dataset = load_dataset(DATASET_DIR)
    ingest_real_meetings(conn, dataset)
    assert check_against_db(dataset, conn) == []


def test_real_meetings_ingest_into_eval_db(conn) -> None:
    """真实会议灌进评测库后：5 场会议 / 63 条片段，且金标引用能对上。"""
    from app.store.repos import MeetingRepo, SegmentRepo

    dataset = load_dataset(DATASET_DIR)
    assert ingest_real_meetings(conn, dataset) == 2
    repo = MeetingRepo(conn)
    assert repo.count() == 5
    assert len(SegmentRepo(conn).list_for_meeting("m_real_2")) == 24
    meeting = repo.get("m_real_1")
    assert meeting is not None
    assert meeting.title.startswith("官网改版项目短会")
    assert {p.name for p in meeting.participants} == {"项目经理", "运营专员", "UI设计师", "前端开发"}
    # 合说片段按"未识别"处理，置信度 0.5（领域模型不承载 GROUP）
    group_segments = [
        item for item in SegmentRepo(conn).list_for_meeting("m_real_1") if item.speaker_id is None
    ]
    assert len(group_segments) == 1
    assert group_segments[0].speaker_confidence == 0.5


def test_real_meeting_minutes_gold_is_merged_into_minutes_section() -> None:
    """真实会议的纪要金标只写一次，装载时合并进统一的纪要金标列表。"""
    dataset = load_dataset(DATASET_DIR)
    gold = {item.meeting_id: item for item in dataset.minutes}
    assert "m_real_2" in gold
    assert len(gold["m_real_2"].action_items) == 4
    evidence = gold["m_real_2"].action_items[0].evidence
    assert evidence and evidence[0].startswith("m_real_2:")


def test_cross_meeting_cases_are_covered() -> None:
    dataset = load_dataset(DATASET_DIR)
    cross = [case for case in dataset.retrieval if case.gold_meetings]
    assert len(cross) >= 8, "跨会议检索准确率需要足够的样本"
    outside = [case for case in dataset.retrieval if not case.gold_segments]
    assert len(outside) >= 8, "域外拒答需要足够的样本"


def test_missing_file_raises(tmp_path: Path) -> None:
    for filename in SECTION_FILES.values():
        (tmp_path / filename).write_text(
            json.dumps({"version": "v1", "cases": []}), encoding="utf-8"
        )
    (tmp_path / "qa_gold.json").unlink()
    with pytest.raises(FileNotFoundError):
        load_dataset(tmp_path)


def test_version_mismatch_raises(tmp_path: Path) -> None:
    for filename in SECTION_FILES.values():
        (tmp_path / filename).write_text(
            json.dumps({"version": "v1", "cases": []}), encoding="utf-8"
        )
    (tmp_path / "route_gold.json").write_text(
        json.dumps({"version": "v2", "cases": []}), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_dataset(tmp_path)


def test_missing_version_raises(tmp_path: Path) -> None:
    for filename in SECTION_FILES.values():
        payload = {"cases": []}
        (tmp_path / filename).write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_dataset(tmp_path)


def test_check_flags_unknown_segment(tmp_path: Path, conn) -> None:
    for filename in SECTION_FILES.values():
        (tmp_path / filename).write_text(
            json.dumps({"version": "v1", "cases": []}), encoding="utf-8"
        )
    (tmp_path / "retrieval_gold.json").write_text(
        json.dumps(
            {
                "version": "v1",
                "cases": [{"query": "不存在的片段", "gold_segments": ["m_20260911:s99"]}],
            }
        ),
        encoding="utf-8",
    )
    dataset = load_dataset(tmp_path)
    problems = check_against_db(dataset, conn)
    assert any("金标片段不存在" in item for item in problems)
