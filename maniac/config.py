"""Configuration dataclass and default settings for maniac."""

import importlib.resources
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv

from .exceptions import ManiacError

# LiteLLM otherwise fetches a remote pricing map on import; config.py is
# imported before litellm anywhere in the app, so setting this here covers
# every import site.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

_XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
_XDG_CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
_XDG_DATA = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
_XDG_STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
_CONFIG_DIR = _XDG_CONFIG / "maniac"
_CONFIG_TOML = _CONFIG_DIR / "config.toml"


def _load_defaults() -> dict[str, Any]:
    """Read the packaged provider defaults, limits and timeouts."""
    text = (
        importlib.resources.files("maniac")
        .joinpath("defaults.toml")
        .read_text(encoding="utf-8")
    )
    return tomllib.loads(text)


_DEFAULTS = _load_defaults()
PROVIDER_DEFAULTS: dict[str, str] = cast(dict[str, str], _DEFAULTS["providers"])
_LIMITS: dict[str, int] = cast(dict[str, int], _DEFAULTS["limits"])
_TIMEOUTS: dict[str, int] = cast(dict[str, int], _DEFAULTS["timeouts"])


def _load_config_env(config_dir: Path) -> None:
    load_dotenv(config_dir / ".env", override=False)


def _load_config_file(config_dir: Path) -> dict[str, Any]:
    """Read the user's provider/model/reasoning_effort settings."""
    config_file = config_dir / "config.toml"
    if not config_file.is_file():
        legacy_file = config_dir / "config.yaml"
        if legacy_file.is_file():
            raise ValueError(
                f"{legacy_file} found, but maniac now reads {config_file} "
                "(TOML, not YAML). There is no automatic migration: recreate "
                "'provider', 'model' and/or 'reasoning_effort' in the new file."
            )
        return {}

    try:
        contents = tomllib.loads(config_file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"Invalid configuration file: {config_file}") from e

    return {
        key: contents[key]
        for key in ("provider", "model", "reasoning_effort")
        if key in contents and isinstance(contents[key], str)
    }


_CONFIG_VALUES = _load_config_file(_CONFIG_DIR)
_load_config_env(_CONFIG_DIR)


def _configured_reasoning_effort() -> str | None:
    """config.toml's `reasoning_effort` outranks MANIAC_REASONING_EFFORT, matching resolve_model's chain."""
    return _CONFIG_VALUES.get("reasoning_effort") or os.environ.get(
        "MANIAC_REASONING_EFFORT"
    )


def _provider_default_model(provider: str) -> str:
    if provider not in PROVIDER_DEFAULTS:
        raise ManiacError(
            f"Unknown provider '{provider}' in {_CONFIG_TOML}. "
            f"Configured providers: {', '.join(PROVIDER_DEFAULTS)}."
        )
    return PROVIDER_DEFAULTS[provider]


def _sniff_provider() -> str:
    """Pick the first configured provider whose API key is present in the environment."""
    import litellm

    present: list[str] = []
    checked: list[str] = []
    for provider, model in PROVIDER_DEFAULTS.items():
        result = litellm.validate_environment(model=model)
        if result["keys_in_environment"]:
            present.append(provider)
        else:
            checked.extend(k for k in result["missing_keys"] if k not in checked)

    if not present:
        raise ManiacError(
            "No LLM API key found. Set one of "
            f"{', '.join(checked)} in the environment (or {_CONFIG_DIR / '.env'}), "
            f"or set 'model'/'provider' in {_CONFIG_TOML}."
        )

    chosen = present[0]
    if len(present) > 1:
        print(
            f"maniac: multiple provider API keys present ({', '.join(present)}); "
            f"using '{chosen}'. Set provider = \"{chosen}\" in {_CONFIG_TOML} "
            "to pin it.",
            file=sys.stderr,
        )
    return chosen


def _validate_model(model: str) -> None:
    """Raise if LiteLLM's own provider registry does not recognize the model."""
    import litellm

    provider, _, name = model.partition("/")
    known = litellm.models_by_provider.get(provider, [])
    if name not in known and model not in known:
        raise ManiacError(
            f"Model '{model}' is not recognized by LiteLLM. Set 'model' in "
            f"{_CONFIG_TOML} to a valid LiteLLM model identifier."
        )


@dataclass
class Config:
    """Central configuration for paths, limits, and timeouts."""

    config_dir: Path = field(default_factory=lambda: _CONFIG_DIR)
    cache_dir: Path = field(default_factory=lambda: _XDG_CACHE / "maniac" / "repos")
    output_dir: Path = field(default_factory=lambda: _XDG_DATA / "maniac" / "manpages")
    man_dir: Path = field(default_factory=lambda: _XDG_DATA / "man" / "man1")
    intermediate_dir: Path = field(
        default_factory=lambda: _XDG_STATE / "maniac" / "intermediate"
    )
    bench_dir: Path = field(default_factory=lambda: _XDG_STATE / "maniac" / "bench")
    manifest_path: Path = field(
        default_factory=lambda: _XDG_STATE / "maniac" / "installed.json"
    )
    backup_dir: Path = field(default_factory=lambda: _XDG_STATE / "maniac" / "backups")
    llm_api_key: str | None = field(
        default_factory=lambda: os.environ.get("MANIAC_LLM_API_KEY")
    )
    llm_reasoning_effort: str | None = field(
        default_factory=_configured_reasoning_effort
    )
    max_arg_limit: int = _LIMITS["max_arg_limit"]
    max_total_doc_chars: int = _LIMITS["max_total_doc_chars"]
    timeout_help: int = _TIMEOUTS["help"]
    timeout_llm: int = _TIMEOUTS["llm"]
    timeout_git: int = _TIMEOUTS["git"]
    timeout_pandoc: int = _TIMEOUTS["pandoc"]

    def resolve_model(self, model: str | None = None) -> str:
        """Resolve a model identifier: explicit argument, config.toml, MANIAC_MODEL, sniffed provider.

        A `model` set in config.toml wins over a `provider` set there; both
        outrank MANIAC_MODEL, which outranks sniffing the environment for
        the first configured provider whose API key is present.
        """
        if model:
            resolved = model
        elif _CONFIG_VALUES.get("model"):
            resolved = _CONFIG_VALUES["model"]
        elif _CONFIG_VALUES.get("provider"):
            resolved = _provider_default_model(_CONFIG_VALUES["provider"])
        elif os.environ.get("MANIAC_MODEL"):
            resolved = os.environ["MANIAC_MODEL"]
        else:
            resolved = _provider_default_model(_sniff_provider())

        resolved = resolved.strip()
        _validate_model(resolved)
        return resolved

    def resolve_reasoning_effort(self) -> str | None:
        """Return the configured global reasoning effort, if any."""
        return self.llm_reasoning_effort
