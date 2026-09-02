"""Configuration loading tests."""

import os
from pathlib import Path

import pytest

from maniac.config import Config, _load_config_env, _load_config_file


def test_load_config_env_preserves_shell_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MANIAC_LLM_API_KEY=dotenv-key\nGEMINI_API_KEY=gemini-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MANIAC_LLM_API_KEY", "shell-key")

    _load_config_env(tmp_path)

    assert Config().llm_api_key == "shell-key"


def test_load_config_env_makes_gemini_key_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    (tmp_path / ".env").write_text("GEMINI_API_KEY=gemini-key\n", encoding="utf-8")
    monkeypatch.delenv("MANIAC_LLM_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    _load_config_env(tmp_path)

    assert os.environ["GEMINI_API_KEY"] == "gemini-key"


def test_load_config_file_uses_simple_llm_settings(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        "backend: litellm\nmodel: gemini/gemini-3.5-flash-lite\nreasoning_effort: low\n",
        encoding="utf-8",
    )

    assert _load_config_file(tmp_path) == {
        "backend": "litellm",
        "model": "gemini/gemini-3.5-flash-lite",
        "reasoning_effort": "low",
    }


def test_environment_overrides_config_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setattr(
        "maniac.config._CONFIG_VALUES",
        {"backend": "agy", "model": "flash-low", "reasoning_effort": "high"},
    )
    monkeypatch.setenv("MANIAC_MODEL", "gemini/gemini-3.5-flash")

    cfg = Config()

    assert cfg.llm_backend == "agy"
    assert cfg.default_model == "gemini/gemini-3.5-flash"
    assert cfg.llm_reasoning_effort == "high"


def test_non_gemini_model_omits_default_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.delenv("MANIAC_MODEL", raising=False)
    monkeypatch.delenv("MANIAC_REASONING_EFFORT", raising=False)
    monkeypatch.setattr(
        "maniac.config._CONFIG_VALUES",
        {"model": "openrouter/anthropic/claude-3.5-sonnet"},
    )

    cfg = Config()

    assert cfg.llm_reasoning_effort is None
    assert cfg.resolve_reasoning_effort() is None


def test_model_override_uses_its_own_default_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.delenv("MANIAC_REASONING_EFFORT", raising=False)
    monkeypatch.setattr("maniac.config._CONFIG_VALUES", {})

    cfg = Config()

    assert cfg.resolve_reasoning_effort("gemini/gemini-3.5-flash") == "low"
    assert cfg.resolve_reasoning_effort("anthropic/claude-sonnet-4-6") is None
