"""Configuration dataclass and default settings for maniac."""

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODEL_ALIASES: dict[str, str] = {
    "flash": "Gemini 3.7 Flash (High)",
    "flash-high": "Gemini 3.7 Flash (High)",
    "flash-medium": "Gemini 3.7 Flash (Medium)",
    "flash-low": "Gemini 3.7 Flash (Low)",
    "pro": "Gemini 3.1 Pro (High)",
    "pro-low": "Gemini 3.1 Pro (Low)",
    "sonnet": "Claude Sonnet 4.6 (Thinking)",
    "opus": "Claude Opus 4.6 (Thinking)",
}


@dataclass
class Config:
    """Central configuration for paths, model aliases, and limits."""

    cache_dir: Path = field(default_factory=lambda: Path("data/repos"))
    output_dir: Path = field(default_factory=lambda: Path("data/manpages"))
    intermediate_dir: Path = field(default_factory=lambda: Path("data/intermediate"))
    work_base_dir: Path = field(default_factory=lambda: Path("data/tmp"))
    man_dir: Path = field(
        default_factory=lambda: Path("~/.local/share/man/man1").expanduser()
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
