from pathlib import Path

import pytest

import maniac.config as config_module
from maniac.sources.pathcache import resolve_cached


@pytest.fixture(autouse=True)
def _clear_resolve_cache() -> None:
    """`resolve_cached` is process-lifetime; a test-lifetime cache would leak
    a resolution across tests if two ever produced the same `Path` string
    for different underlying filesystem state (unlikely given `tmp_path` is
    unique per test, but cheap to rule out).
    """
    resolve_cached.cache_clear()


@pytest.fixture(autouse=True)
def _no_real_xdg_writes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect every Config default path under tmp_path, suite-wide.

    `Config`'s defaults come from module-level `_XDG_*` globals computed at
    import time, so an ordinary env-var monkeypatch after import has no
    effect. Patching those globals directly means any test that forgets to
    pass an explicit dir still lands in tmp_path rather than the real
    `~/.local/state/maniac/` etc (M1).

    This lives in conftest rather than one test module because the leak is
    not specific to the pipeline: any entry point that builds a default
    `Config` reintroduces it, as `test_cli_install_dry_run` did.
    """
    for attr, sub in (
        ("_XDG_CONFIG", "config"),
        ("_XDG_CACHE", "cache"),
        ("_XDG_DATA", "data"),
        ("_XDG_STATE", "state"),
    ):
        monkeypatch.setattr(config_module, attr, tmp_path / sub)
