"""配置层测试：.env 载入、LLM 模式解析、检索参数覆盖与非法值回落。"""

from __future__ import annotations

import os
from dataclasses import replace

import app.config as config
from app.config import RetrievalSettings, Settings

LLM_ENV_KEYS = (
    "MA_LLM_PROVIDER",
    "MA_LLM_MODE",
    "MA_LLM_BASE_URL",
    "MA_LLM_API_KEY",
    "MA_LLM_MODEL_ROUTE",
    "MA_LLM_MODEL_ANSWER",
    "MA_LLM_MODEL_MINUTES",
    "MA_ROUTE_MODE",
    "MA_VERIFY_MODE",
    "MA_TEXT_ENCODER",
)


def clear_llm_env(monkeypatch) -> None:
    for name in LLM_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_resolve_to_off_without_key(tmp_path, monkeypatch) -> None:
    """默认配置（无 Key）必须落在 off：新克隆的机器不会误发请求。"""
    clear_llm_env(monkeypatch)
    settings = Settings.from_env(tmp_path / "data")
    assert settings.llm_mode == "auto"
    assert settings.effective_llm_mode == "off"
    assert settings.llm_available is False
    assert "MA_LLM_API_KEY" in settings.llm_mode_reason


def test_auto_with_key_becomes_live(tmp_path, monkeypatch) -> None:
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("MA_LLM_API_KEY", "sk-test-not-a-real-key")
    settings = Settings.from_env(tmp_path / "data")
    assert settings.effective_llm_mode == "live"
    assert settings.llm_available is True
    assert settings.llm_mode_reason == ""


def test_live_without_key_degrades_to_off(tmp_path, monkeypatch) -> None:
    """显式配了 live 但没 Key 时降级而不是崩：真实失败原因要看得见。"""
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("MA_LLM_MODE", "live")
    settings = Settings.from_env(tmp_path / "data")
    assert settings.effective_llm_mode == "off"
    assert "MA_LLM_API_KEY" in settings.llm_mode_reason


def test_defaults_match_deepseek_openai_compatible(tmp_path, monkeypatch) -> None:
    """决策 ①：provider 是协议名 openai，默认打 DeepSeek 的兼容端点。"""
    clear_llm_env(monkeypatch)
    settings = Settings.from_env(tmp_path / "data")
    assert settings.llm_provider == "openai"
    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_model_answer == "deepseek-flash"


def test_invalid_enum_values_fall_back(tmp_path, monkeypatch) -> None:
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("MA_LLM_PROVIDER", "anthropic-native")
    monkeypatch.setenv("MA_LLM_MODE", "sometimes")
    monkeypatch.setenv("MA_ROUTE_MODE", "magic")
    monkeypatch.setenv("MA_VERIFY_MODE", "vibes")
    monkeypatch.setenv("MA_TEXT_ENCODER", "bge-m3")
    settings = Settings.from_env(tmp_path / "data")
    assert settings.llm_provider == "openai"
    assert settings.llm_mode == "auto"
    assert settings.route_mode == "auto"
    assert settings.verify_mode == "rule"
    assert settings.text_encoder == "auto"


def test_dotenv_is_loaded_but_real_env_wins(tmp_path, monkeypatch) -> None:
    """`.env` 只补空缺：真实环境变量优先，且文件不存在时不报错。"""
    (tmp_path / ".env").write_text("MA_TEST_DOTENV=from_file\n", encoding="utf-8")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setenv("MA_TEST_DOTENV", "from_env")

    assert config.load_project_env() is True
    assert os.environ["MA_TEST_DOTENV"] == "from_env"

    monkeypatch.delenv("MA_TEST_DOTENV")
    assert config.load_project_env() is True
    assert os.environ["MA_TEST_DOTENV"] == "from_file"

    monkeypatch.setattr(config, "ROOT", tmp_path / "not-there")
    assert config.load_project_env() is False


def test_retrieval_settings_are_overridable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MA_RECALL_K1", "11")
    monkeypatch.setenv("MA_RECALL_K2", "22")
    monkeypatch.setenv("MA_RRF_K", "7")
    monkeypatch.setenv("MA_MIN_COSINE", "0.5")
    monkeypatch.setenv("MA_RETRIEVAL_TOP_K", "3")
    settings = Settings.from_env(tmp_path / "data")
    assert settings.retrieval.recall_k1 == 11
    assert settings.retrieval.recall_k2 == 22
    assert settings.retrieval.rrf_k == 7
    assert settings.retrieval.min_cosine == 0.5
    assert settings.retrieval.top_k == 3


def test_retrieval_settings_defaults_and_replaced() -> None:
    base = RetrievalSettings()
    assert (base.recall_k1, base.recall_k2, base.rrf_k) == (50, 50, 60)
    swept = base.replaced(rrf_k=10)
    assert swept.rrf_k == 10
    assert base.rrf_k == 60, "replaced 不能改到原对象"


def test_embedding_model_dir_resolves_against_project_root(tmp_path, monkeypatch) -> None:
    clear_llm_env(monkeypatch)
    settings = Settings.from_env(tmp_path / "data")
    assert settings.resolved_embedding_model_dir == config.ROOT / "model" / "bge-small-zh-v1.5"

    custom = replace(settings, embedding_model_dir=str(tmp_path / "custom"))
    assert custom.resolved_embedding_model_dir == tmp_path / "custom"
