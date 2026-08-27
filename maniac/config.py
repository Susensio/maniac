"""Configuration dataclass and default settings for maniac."""

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODEL_ALIASES: dict[str, str] = {
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

_XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
_XDG_CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
_XDG_DATA = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
_XDG_STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")


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
    model_aliases: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_MODEL_ALIASES)
    )
    default_model: str = field(
        default_factory=lambda: os.environ.get("MANIAC_MODEL", "flash")
    )
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
        return self.model_aliases.get(raw_model.lower(), raw_model)
