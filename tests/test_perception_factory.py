"""感知后端工厂：默认离线、取值校验、回落与严格模式。"""

from __future__ import annotations

import dataclasses

import pytest

from app.config import DEFAULT_PERCEPTION_BACKEND, Settings
from app.perception.base import PerceptionError
from app.perception.factory import select_backend


def test_default_backend_is_offline(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MA_PERCEPTION_BACKEND", raising=False)
    settings = Settings.from_env(tmp_path / "data")
    assert settings.perception_backend == DEFAULT_PERCEPTION_BACKEND
    selection = select_backend(settings)
    assert selection.backend.name == "offline"
    assert selection.is_fallback is False
    assert selection.fallback_reason is None


def test_unknown_backend_value_is_rejected(settings: Settings) -> None:
    broken = dataclasses.replace(settings, perception_backend="whisperx")
    with pytest.raises(PerceptionError):
        select_backend(broken)


def test_local_falls_back_to_offline_with_reason(settings: Settings) -> None:
    """依赖没装或权重缺失时不能把异常抛给用户，要回落并说明原因。"""
    local = dataclasses.replace(
        settings, perception_backend="local", whisper_model="no-such-model-dir-xyz"
    )
    selection = select_backend(local)
    assert selection.backend.name == "offline"
    assert selection.requested == "local"
    assert selection.is_fallback is True
    assert "local" in (selection.fallback_reason or "")


def test_strict_mode_raises_instead_of_falling_back(settings: Settings) -> None:
    """MA_PERCEPTION_STRICT=1：宁可启动失败，也不要悄悄跑固定数据。"""
    strict = dataclasses.replace(
        settings,
        perception_backend="local",
        whisper_model="no-such-model-dir-xyz",
        strict_perception=True,
    )
    with pytest.raises(PerceptionError):
        select_backend(strict)