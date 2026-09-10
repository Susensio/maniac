"""Configuration loading and model/provider resolution tests."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from maniac.config import Config, _load_config_env, _load_config_file
from maniac.exceptions import ManiacError


def test_import_does_not_read_environment_or_configuration_files() -> None:
    script = textwrap.dedent(
        """
        import importlib.resources
        import os
        from pathlib import Path

        class FailingEnvironment(dict):
            def get(self, *args, **kwargs):
                raise AssertionError("config import read the environment")

        def fail(*args, **kwargs):
            raise AssertionError("config import read a configuration file")

        os.environ = FailingEnvironment(os.environ)
        Path.home = classmethod(fail)
        importlib.resources.files = fail

        import maniac.config
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_config_construction_binds_xdg_config_file_and_model_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for attr in ("_XDG_CONFIG", "_XDG_CACHE", "_XDG_DATA", "_XDG_STATE"):
        monkeypatch.setattr("maniac.config." + attr, None)

    xdg_config = tmp_path / "config"
    config_dir = xdg_config / "maniac"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text('reasoning_effort = "low"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MANIAC_MODEL", "openai/gpt-5.1-chat-latest")

    cfg = Config()

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "other-config"))
    monkeypatch.setenv("MANIAC_MODEL", "gemini/gemini-3.7-flash")

    assert cfg.config_dir == config_dir
    assert cfg.cache_dir == tmp_path / "cache" / "maniac" / "repos"
    assert cfg.output_dir == tmp_path / "data" / "maniac" / "manpages"
    assert cfg.intermediate_dir == tmp_path / "state" / "maniac" / "intermediate"
    assert cfg.resolve_reasoning_effort() == "low"
    assert cfg.resolve_model() == "openai/gpt-5.1-chat-latest"


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
    (tmp_path / "config.toml").write_text(
        'provider = "anthropic"\n'
        'model = "anthropic/claude-sonnet-4-6"\n'
        'reasoning_effort = "low"\n',
        encoding="utf-8",
    )

    assert _load_config_file(tmp_path) == {
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-6",
        "reasoning_effort": "low",
    }


def test_load_config_file_missing_returns_empty(tmp_path: Path) -> None:
    assert _load_config_file(tmp_path) == {}


def test_load_config_file_rejects_invalid_toml(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("model = [unterminated\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid configuration file"):
        _load_config_file(tmp_path)


def test_load_config_file_legacy_yaml_names_new_format(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("model: gemini/gemini-flash-latest\n")

    with pytest.raises(ValueError, match="config.toml"):
        _load_config_file(tmp_path)


def test_resolve_model_prefers_explicit_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "maniac.config._load_config_file",
        lambda config_dir: {"model": "anthropic/claude-sonnet-4-6"},
    )
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setenv("MANIAC_MODEL", "openai/gpt-5.1-chat-latest")

    cfg = Config()

    assert (
        cfg.resolve_model("gemini/gemini-flash-latest") == "gemini/gemini-flash-latest"
    )


def test_resolve_model_config_file_model_outranks_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "maniac.config._load_config_file",
        lambda config_dir: {"model": "anthropic/claude-sonnet-4-6"},
    )
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setenv("MANIAC_MODEL", "openai/gpt-5.1-chat-latest")

    cfg = Config()

    assert cfg.resolve_model() == "anthropic/claude-sonnet-4-6"


def test_resolve_model_config_file_provider_maps_to_its_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "maniac.config._load_config_file", lambda config_dir: {"provider": "anthropic"}
    )
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.delenv("MANIAC_MODEL", raising=False)

    cfg = Config()

    assert cfg.resolve_model() == "anthropic/claude-sonnet-4-6"


def test_resolve_model_unknown_configured_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "maniac.config._load_config_file",
        lambda config_dir: {"provider": "not-a-provider"},
    )

    with pytest.raises(ManiacError, match="Unknown provider"):
        Config().resolve_model()


def test_resolve_model_falls_back_to_manaic_model_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("maniac.config._load_config_file", lambda config_dir: {})
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setenv("MANIAC_MODEL", "openai/gpt-5.1-chat-latest")

    cfg = Config()

    assert cfg.resolve_model() == "openai/gpt-5.1-chat-latest"


def test_resolve_model_sniffs_provider_when_nothing_else_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import litellm

    monkeypatch.setattr("maniac.config._load_config_file", lambda config_dir: {})
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.delenv("MANIAC_MODEL", raising=False)
    monkeypatch.setattr(
        litellm,
        "validate_environment",
        lambda model: (
            {"keys_in_environment": model.startswith("anthropic/"), "missing_keys": []}
            if model.startswith("anthropic/")
            else {"keys_in_environment": False, "missing_keys": ["GEMINI_API_KEY"]}
        ),
    )

    cfg = Config(
        provider_defaults={
            "gemini": "gemini/gemini-flash-latest",
            "anthropic": "anthropic/claude-sonnet-4-6",
        }
    )

    assert cfg.resolve_model() == "anthropic/claude-sonnet-4-6"


def test_resolve_model_zero_keys_names_config_path_and_checked_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import litellm

    monkeypatch.setattr("maniac.config._load_config_file", lambda config_dir: {})
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.delenv("MANIAC_MODEL", raising=False)
    monkeypatch.setattr(
        litellm,
        "validate_environment",
        lambda model: {
            "keys_in_environment": False,
            "missing_keys": ["GEMINI_API_KEY"],
        },
    )

    with pytest.raises(ManiacError) as exc_info:
        Config(
            provider_defaults={"gemini": "gemini/gemini-flash-latest"}
        ).resolve_model()

    assert "GEMINI_API_KEY" in str(exc_info.value)
    assert "config.toml" in str(exc_info.value)


def test_resolve_model_multiple_keys_prints_one_stderr_line_and_picks_first(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import litellm

    monkeypatch.setattr("maniac.config._load_config_file", lambda config_dir: {})
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.delenv("MANIAC_MODEL", raising=False)
    monkeypatch.setattr(
        litellm,
        "validate_environment",
        lambda model: {"keys_in_environment": True, "missing_keys": []},
    )

    model = Config(
        provider_defaults={
            "gemini": "gemini/gemini-flash-latest",
            "anthropic": "anthropic/claude-sonnet-4-6",
        }
    ).resolve_model()

    assert model == "gemini/gemini-flash-latest"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert "gemini" in captured.err
    assert "anthropic" in captured.err


def test_resolve_model_validates_against_litellm_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ManiacError, match="not recognized"):
        Config().resolve_model("gemini/not-a-real-model")


def test_resolve_reasoning_effort_returns_configured_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "maniac.config._load_config_file",
        lambda config_dir: {"reasoning_effort": "high"},
    )
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.delenv("MANIAC_REASONING_EFFORT", raising=False)

    assert Config().resolve_reasoning_effort() == "high"


def test_resolve_reasoning_effort_defaults_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("maniac.config._load_config_file", lambda config_dir: {})
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.delenv("MANIAC_REASONING_EFFORT", raising=False)

    assert Config().resolve_reasoning_effort() is None
