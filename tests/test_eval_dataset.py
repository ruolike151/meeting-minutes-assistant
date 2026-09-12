"""数据集契约（M5 T0）：schema、金标对账与覆盖度。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.eval.dataset import SECTION_FILES, check_against_db, load_dataset

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


def test_dataset_matches_seed_database(conn) -> None:
    """金标片段、会议都必须真实存在——写错 ID 要当场报错，而不是静默低分。"""
    dataset = load_dataset(DATASET_DIR)
    assert check_against_db(dataset, conn) == []


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
