from pathlib import Path

import pytest

from maniac.sources.loginpath import login_path
from maniac.sources.pathcache import resolve_cached


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
