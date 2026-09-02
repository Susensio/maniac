"""Configuration dataclass and default settings for maniac."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from yaml import YAMLError, safe_load

AGY_MODEL_ALIASES: dict[str, str] = {
    "flash": "Gemini 3.7 Flash (High)",
    "flash-high": "Gemini 3.7 Flash (High)",
    "flash-medium": "Gemini 3.7 Flash (Medium)",
    "flash-low": "Gemini 3.7 Flash (Low)",
    "flash-3.5": "Gemini 3.5 Flash (Low)",
    "flash-3.5-low": "Gemini 3.5 Flash (Low)",
    "pro": "Gemini 3.1 Pro (High)",
    "pro-low": "Gemini 3.1 Pro (Low)",
    "sonnet": "Claude Sonnet 4.6 (Thinking)",
    "opus": "Claude Opus 4.6 (Thinking)",
}

LITELLM_MODEL_ALIASES: dict[str, str] = {
    "flash": "gemini/gemini-3.5-flash",
    "flash-high": "gemini/gemini-3.5-flash",
    "flash-medium": "gemini/gemini-3.5-flash",
    "flash-low": "gemini/gemini-3.5-flash-lite",
    "flash-3.5": "gemini/gemini-3.5-flash",
    "flash-3.5-low": "gemini/gemini-3.5-flash-lite",
    "pro": "gemini/gemini-3.1-pro",
    "pro-low": "gemini/gemini-3.1-pro",
    "sonnet": "anthropic/claude-sonnet-4-6",
    "opus": "anthropic/claude-opus-4-6",
}

_XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
_XDG_CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
_XDG_DATA = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
_XDG_STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")


def _load_config_env(config_dir: Path) -> None:
    load_dotenv(config_dir / ".env", override=False)


def _load_config_file(config_dir: Path) -> dict[str, str]:
    config_file = config_dir / "config.yaml"
    if not config_file.is_file():
        return {}

    try:
        contents = safe_load(config_file.read_text(encoding="utf-8"))
    except YAMLError as e:
        raise ValueError(f"Invalid configuration file: {config_file}") from e

    if contents is None:
        return {}
    if not isinstance(contents, dict):
        raise TypeError(f"Configuration file must contain a mapping: {config_file}")

    settings = {
        key: contents[key]
        for key in ("backend", "model", "reasoning_effort")
        if key in contents
    }
    return {key: value for key, value in settings.items() if isinstance(value, str)}


_CONFIG_DIR = _XDG_CONFIG / "maniac"
_CONFIG_VALUES = _load_config_file(_CONFIG_DIR)
_load_config_env(_CONFIG_DIR)


def _configured_model() -> str:
    return os.environ.get("MANIAC_MODEL", _CONFIG_VALUES.get("model", "flash"))


def _configured_reasoning_effort() -> str | None:
    return os.environ.get(
        "MANIAC_REASONING_EFFORT", _CONFIG_VALUES.get("reasoning_effort")
    )


@dataclass
class Config:
    """Central configuration for paths, model aliases, and limits."""

    config_dir: Path = field(default_factory=lambda: _XDG_CONFIG / "maniac")
    cache_dir: Path = field(default_factory=lambda: _XDG_CACHE / "maniac" / "repos")
    work_base_dir: Path = field(default_factory=lambda: _XDG_CACHE / "maniac" / "tmp")
    output_dir: Path = field(default_factory=lambda: _XDG_DATA / "maniac" / "manpages")
    man_dir: Path = field(default_factory=lambda: _XDG_DATA / "man" / "man1")
    intermediate_dir: Path = field(
        default_factory=lambda: _XDG_STATE / "maniac" / "intermediate"
    )
    bench_dir: Path = field(default_factory=lambda: _XDG_STATE / "maniac" / "bench")
    llm_backend: str = field(
        default_factory=lambda: os.environ.get(
            "MANIAC_LLM_BACKEND", _CONFIG_VALUES.get("backend", "litellm")
        )
    )
    llm_api_key: str | None = field(
        default_factory=lambda: os.environ.get("MANIAC_LLM_API_KEY")
    )
    llm_reasoning_effort: str | None = field(
        default_factory=_configured_reasoning_effort
    )
    default_model: str = field(default_factory=_configured_model)
    max_arg_limit: int = field(
        default_factory=lambda: int(os.environ.get("MANIAC_MAX_ARG_LIMIT", "115000"))
    )
    max_total_doc_chars: int = 75_000
    timeout_help: int = 5
    timeout_llm: int = 180
    timeout_git: int = 60
    timeout_pandoc: int = 15

    def resolve_model(self, model: str | None) -> str:
        """Resolve a model name or alias to the full model identifier."""
        raw_model = (model or self.default_model).strip()
        aliases = (
            AGY_MODEL_ALIASES if self.llm_backend == "agy" else LITELLM_MODEL_ALIASES
        )
        return aliases.get(raw_model.lower(), raw_model)

    def resolve_reasoning_effort(self, model: str | None = None) -> str | None:
        """Resolve an explicit effort or Gemini's default for the selected model."""
        if self.llm_reasoning_effort is not None:
            return self.llm_reasoning_effort
        return "low" if self.resolve_model(model).startswith("gemini/") else None
