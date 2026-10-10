"""Providers against real installs: `npm install -g` and `pipx install`.

Each tool is installed for real, once per run, into a throwaway prefix, then
resolved through maniac exactly as a user's `$PATH` would reach it. Unit
tests build these layouts by hand; these check that the hand-built layouts
are the ones the installers actually produce.

Needs network access (the npm registry and PyPI), `npm`, and `uvx` -- pipx
runs through `uvx`, pinned, so no pipx install is needed.
"""

import os
import subprocess
from pathlib import Path

import pytest

from maniac.config import Config
from maniac.manifest import Tier
from maniac.orchestration.install import run_install
from maniac.sources.providers.registry import registry
from maniac.sources.resolution import find_installation

from .conftest import _require, _run

PIPX_VERSION = "1.8.0"
PIPX_PACKAGE = "cowsay==6.1"


@pytest.fixture(scope="session")
def pipx_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`pipx install` under its own `PIPX_HOME`, bins linked from `PIPX_HOME/bin`."""
    home = tmp_path_factory.mktemp("pipx")
    env = {
        **os.environ,
        "PIPX_HOME": str(home),
        "PIPX_BIN_DIR": str(home / "bin"),
        "PIPX_MAN_DIR": str(home / "man"),
    }
    _run([_require("uvx"), f"pipx=={PIPX_VERSION}", "install", PIPX_PACKAGE], env=env)
    return home


def _on_path(monkeypatch: pytest.MonkeyPatch, bin_dir: Path) -> None:
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


@pytest.mark.parametrize("binary", ["cowsay", "cowthink"])
def test_npm_global_install_is_claimed_with_its_package_json(
    npm_prefix: Path, monkeypatch: pytest.MonkeyPatch, binary: str
) -> None:
    """Both of a package's bins name the package, its version and the
    repository its `package.json` declares."""
    _on_path(monkeypatch, npm_prefix / "bin")

    found = find_installation(binary)

    assert found is not None
    provider, inst = found
    assert provider.name == "npm"
    assert (inst.package, inst.version) == ("cowsay", "1.6.0")
    assert inst.root == npm_prefix / "lib" / "node_modules" / "cowsay"
    source = registry.resolve_source(inst, config=Config(), provider=provider)
    assert source is not None
    assert source.identity == "piuccio/cowsay"


def test_npm_package_manpage_installs_from_the_install_root(
    npm_prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tier 1 for real: `marked` ships `man/marked.1`; maniac links it
    into the man root, and `man` reaches it -- no crawl, no model."""
    _on_path(monkeypatch, npm_prefix / "bin")
    cfg = Config()
    monkeypatch.setenv("MANPATH", str(cfg.man_dir.parent))

    outcome = run_install("marked", config=cfg, no_synthesize=True)

    assert outcome.tier is Tier.INSTALL_ROOT
    assert outcome.installed_path is not None
    shipped = npm_prefix / "lib" / "node_modules" / "marked" / "man" / "marked.1"
    # Linked, not copied (ADR-0028): the page follows the package it ships in.
    assert outcome.installed_path == cfg.man_dir / "marked.1"
    assert outcome.installed_path.is_symlink()
    assert outcome.installed_path.resolve() == shipped.resolve()
    where = subprocess.run(
        ["man", "-w", "marked"], capture_output=True, text=True, check=False
    )
    assert where.returncode == 0, where.stderr
    assert Path(where.stdout.strip()).resolve() == shipped.resolve()


def test_pipx_install_is_claimed_with_its_dist_info(
    pipx_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bin pipx links into `PIPX_HOME/bin` resolves into its venv; the
    package, version and repository come from the dist-info pipx installed."""
    monkeypatch.setenv("PIPX_HOME", str(pipx_home))
    _on_path(monkeypatch, pipx_home / "bin")

    found = find_installation("cowsay")

    assert found is not None
    provider, inst = found
    assert provider.name == "pipx"
    assert (inst.package, inst.version) == ("cowsay", "6.1")
    assert inst.root == pipx_home / "venvs" / "cowsay"
    source = registry.resolve_source(inst, config=Config(), provider=provider)
    assert source is not None
    assert source.identity == "VaasuDevanS/cowsay-python"
