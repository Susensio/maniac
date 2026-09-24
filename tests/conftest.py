from collections.abc import Generator
from pathlib import Path

import pytest
import structlog

from maniac.sources.docs import cache
from maniac.sources.loginpath import login_path
from maniac.sources.pathcache import resolve_cached


@pytest.fixture(autouse=True)
def _reset_structlog() -> Generator[None, None, None]:
    """`structlog.configure()` is process-lifetime global state.

    `maniac list --verbose` and any test that calls `setup_logging` or
    reconfigures structlog directly (a level, a processor chain, a capture
    fixture) leaves that configuration standing for whichever test runs
    next. Resetting to structlog's own built-in defaults before and after
    every test means no test starts polluted by an earlier one, and none
    leaves debug output live for a later test's CLI assertion to trip over.

    Never `monkeypatch.setattr` a structlog logger attribute -- `logger` is
    a `BoundLoggerLazyProxy`, so monkeypatch's restore binds a concrete
    logger and freezes the attribute for the rest of the process; use
    `structlog.testing.capture_logs()`. A prior CLI test's `setup_logging()`
    left level at WARNING, so this fixture bounds it suite-wide.
    """
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()


@pytest.fixture(autouse=True)
def _no_real_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """`cache.py` resolves a token via `resolve_github_token()` on every request.

    Stubbed to `None` suite-wide so an ordinary test never spawns `gh auth
    token` or picks up the developer's real credential -- `resolve_github_token`
    is process-cached, so without this the first call anywhere in the suite
    would decide every later test's token. A test exercising resolution
    itself patches `maniac.github_token.resolve_github_token` directly.
    """
    monkeypatch.setattr(cache, "resolve_github_token", lambda: None)


@pytest.fixture(autouse=True)
def _clear_resolve_cache() -> None:
    """`resolve_cached` is process-lifetime; a test-lifetime cache would leak
    a resolution across tests if two ever produced the same `Path` string
    for different underlying filesystem state (unlikely given `tmp_path` is
    unique per test, but cheap to rule out).

    `login_path` is cleared alongside it for the same reason: also
    process-lifetime, and a test that patches `$SHELL` or `os.environ` would
    otherwise see a prior test's cached result instead of its own.
    """
    resolve_cached.cache_clear()
    login_path.cache_clear()


@pytest.fixture(autouse=True)
def _no_real_xdg_writes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect every Config default path under tmp_path, suite-wide.

    `Config` binds XDG environment variables during construction, so every
    test's default Config lands under tmp_path rather than the real
    `~/.local/state/maniac/` etc (M1).

    This lives in conftest rather than one test module because the leak is
    not specific to the pipeline: any entry point that builds a default
    `Config` reintroduces it, as `test_cli_install_dry_run` did.
    """
    for variable, subdir in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
    ):
        monkeypatch.setenv(variable, str(tmp_path / subdir))
