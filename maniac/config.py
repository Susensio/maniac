"""Configuration dataclass and default settings for maniac."""

import importlib.resources
import os
import sys
import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv

from .exceptions import ManiacError

# LiteLLM otherwise fetches a remote pricing map on import; config.py is
# imported before litellm anywhere in the app, so setting this here covers
# every import site.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

# TODO: Remove these fixture compatibility hooks when the suite-wide XDG fixture
# changes to set environment variables in ADR-0022 step 4.
_XDG_CONFIG: Path | None = None
_XDG_CACHE: Path | None = None
_XDG_DATA: Path | None = None
_XDG_STATE: Path | None = None
_UNSET = object()


def _xdg_config_dir() -> Path:
    return _XDG_CONFIG or Path(
        os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    )


def _xdg_cache_dir() -> Path:
    return _XDG_CACHE or Path(
        os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    )


def _xdg_data_dir() -> Path:
    return _XDG_DATA or Path(
        os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    )


def _xdg_state_dir() -> Path:
    return _XDG_STATE or Path(
        os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    )


def _config_dir() -> Path:
    return _xdg_config_dir() / "maniac"


@cache
def _load_defaults() -> dict[str, Any]:
    """Read the packaged provider defaults, limits and timeouts."""
    text = (
        importlib.resources.files("maniac")
        .joinpath("defaults.toml")
        .read_text(encoding="utf-8")
    )
    return tomllib.loads(text)


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


def _configured_reasoning_effort(config_values: dict[str, Any]) -> str | None:
    """config.toml's `reasoning_effort` outranks MANIAC_REASONING_EFFORT, matching resolve_model's chain."""
    return config_values.get("reasoning_effort") or os.environ.get(
        "MANIAC_REASONING_EFFORT"
    )


def _configured_model(
    config_values: dict[str, Any],
    model_from_environment: str | None,
    provider_defaults: dict[str, str],
    config_file: Path,
) -> str | None:
    if config_values.get("model"):
        return config_values["model"].strip()
    if config_values.get("provider"):
        return _provider_default_model(
            config_values["provider"], provider_defaults, config_file
        ).strip()
    if model_from_environment:
        return model_from_environment.strip()
    return None


def _provider_default_model(
    provider: str, provider_defaults: dict[str, str], config_file: Path
) -> str:
    if provider not in provider_defaults:
        raise ManiacError(
            f"Unknown provider '{provider}' in {config_file}. "
            f"Configured providers: {', '.join(provider_defaults)}."
        )
    return provider_defaults[provider]


def _sniff_provider(
    provider_defaults: dict[str, str], config_dir: Path, config_file: Path
) -> str:
    """Pick the first configured provider whose API key is present in the environment."""
    import litellm

    present: list[str] = []
    checked: list[str] = []
    for provider, model in provider_defaults.items():
        result = litellm.validate_environment(model=model)
        if result["keys_in_environment"]:
            present.append(provider)
        else:
            checked.extend(k for k in result["missing_keys"] if k not in checked)

    if not present:
        raise ManiacError(
            "No LLM API key found. Set one of "
            f"{', '.join(checked)} in the environment (or {config_dir / '.env'}), "
            f"or set 'model'/'provider' in {config_file}."
        )

    chosen = present[0]
    if len(present) > 1:
        print(
            f"maniac: multiple provider API keys present ({', '.join(present)}); "
            f"using '{chosen}'. Set provider = \"{chosen}\" in {config_file} "
            "to pin it.",
            file=sys.stderr,
        )
    return chosen


def _validate_model(model: str, config_file: Path) -> None:
    """Raise if LiteLLM's own provider registry does not recognize the model."""
    import litellm

    provider, _, name = model.partition("/")
    known = litellm.models_by_provider.get(provider, [])
    if name not in known and model not in known:
        raise ManiacError(
            f"Model '{model}' is not recognized by LiteLLM. Set 'model' in "
            f"{config_file} to a valid LiteLLM model identifier."
        )


@dataclass
class Config:
    """Central configuration for paths, limits, and timeouts."""

    config_dir: Path = field(default_factory=_config_dir)
    cache_dir: Path = field(
        default_factory=lambda: _xdg_cache_dir() / "maniac" / "repos"
    )
    output_dir: Path = field(
        default_factory=lambda: _xdg_data_dir() / "maniac" / "manpages"
    )
    man_dir: Path = field(default_factory=lambda: _xdg_data_dir() / "man" / "man1")
    intermediate_dir: Path = field(
        default_factory=lambda: _xdg_state_dir() / "maniac" / "intermediate"
    )
    bench_dir: Path = field(
        default_factory=lambda: _xdg_state_dir() / "maniac" / "bench"
    )
    manifest_path: Path = field(
        default_factory=lambda: _xdg_state_dir() / "maniac" / "installed.json"
    )
    backup_dir: Path = field(
        default_factory=lambda: _xdg_state_dir() / "maniac" / "backups"
    )
    llm_api_key: str | None = cast(str | None, _UNSET)
    llm_reasoning_effort: str | None = cast(str | None, _UNSET)
    max_arg_limit: int = field(
        default_factory=lambda: cast(dict[str, int], _load_defaults()["limits"])[
            "max_arg_limit"
        ]
    )
    max_total_doc_chars: int = field(
        default_factory=lambda: cast(dict[str, int], _load_defaults()["limits"])[
            "max_total_doc_chars"
        ]
    )
    timeout_help: int = field(
        default_factory=lambda: cast(dict[str, int], _load_defaults()["timeouts"])[
            "help"
        ]
    )
    timeout_llm: int = field(
        default_factory=lambda: cast(dict[str, int], _load_defaults()["timeouts"])[
            "llm"
        ]
    )
    timeout_git: int = field(
        default_factory=lambda: cast(dict[str, int], _load_defaults()["timeouts"])[
            "git"
        ]
    )
    timeout_pandoc: int = field(
        default_factory=lambda: cast(dict[str, int], _load_defaults()["timeouts"])[
            "pandoc"
        ]
    )
    provider_defaults: dict[str, str] = field(
        default_factory=lambda: dict(
            cast(dict[str, str], _load_defaults()["providers"])
        ),
        repr=False,
        kw_only=True,
    )
    _config_values: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _model_from_environment: str | None = field(default=None, init=False, repr=False)
    _configured_model: str | None = field(default=None, init=False, repr=False)
    _last_resolved_model: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        _load_config_env(self.config_dir)
        self._config_values = _load_config_file(self.config_dir)
        if self.llm_api_key is _UNSET:
            self.llm_api_key = os.environ.get("MANIAC_LLM_API_KEY")
        if self.llm_reasoning_effort is _UNSET:
            self.llm_reasoning_effort = _configured_reasoning_effort(
                self._config_values
            )
        self._model_from_environment = os.environ.get("MANIAC_MODEL")
        self._configured_model = _configured_model(
            self._config_values,
            self._model_from_environment,
            self.provider_defaults,
            self.config_dir / "config.toml",
        )
        if self._configured_model is not None:
            _validate_model(self._configured_model, self.config_dir / "config.toml")

    def resolve_model(self, model: str | None = None) -> str:
        """Resolve a model identifier: explicit argument, config.toml, MANIAC_MODEL, sniffed provider.

        A `model` set in config.toml wins over a `provider` set there; both
        outrank MANIAC_MODEL, which outranks sniffing the environment for
        the first configured provider whose API key is present.
        """
        if model:
            resolved = model.strip()
            _validate_model(resolved, self.config_dir / "config.toml")
        elif self._configured_model is not None:
            return self._configured_model
        elif self._last_resolved_model is not None:
            return self._last_resolved_model
        else:
            resolved = _provider_default_model(
                _sniff_provider(
                    self.provider_defaults,
                    self.config_dir,
                    self.config_dir / "config.toml",
                ),
                self.provider_defaults,
                self.config_dir / "config.toml",
            ).strip()
            _validate_model(resolved, self.config_dir / "config.toml")
        self._last_resolved_model = resolved
        return resolved

    def model_for_metadata(self, model: str | None = None) -> str | None:
        """Return a known model without sniffing provider credentials."""
        return self._last_resolved_model or (
            model.strip() if model else self._configured_model
        )

    def resolve_reasoning_effort(self) -> str | None:
        """Return the configured global reasoning effort, if any."""
        return self.llm_reasoning_effort
