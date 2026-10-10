"""Integration tests: maniac against real pandoc, groff and man (ADR-0064).

Every test under this directory is marked `integration` (see the root
`conftest.py`), deselected from `just test`, and run by `just integration`.
They fail -- never skip -- when a tool they need is missing: a skipped
integration suite reads as green while proving nothing.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
REQUIRED_TOOLS = ("pandoc", "groff", "man")


@pytest.fixture(autouse=True, scope="session")
def _real_tools_present() -> None:
    missing = [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]
    if missing:
        pytest.fail(
            f"integration tests need {', '.join(missing)} on $PATH "
            "(they never skip; run `just test` for the unit suite alone)",
            pytrace=False,
        )


@pytest.fixture
def vanilla_tmac(tmp_path: Path) -> Path:
    """A macro dir that masks distribution `man.local` tweaks.

    Debian's site `man.local` maps every `-` to `\\-`, which hides roff
    that an unpatched groff (Arch, Fedora, macOS Homebrew) renders as
    U+2010 HYPHEN. Passed as `groff -M`, its empty files win the search, so
    a rendering shows what those systems show.
    """
    tmac = tmp_path / "vanilla-tmac"
    tmac.mkdir()
    (tmac / "man.local").touch()
    (tmac / "mdoc.local").touch()
    return tmac


@pytest.fixture
def faketool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`faketool` on `$PATH`, first: a real executable the crawler runs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "faketool"
    shutil.copy(FIXTURES / "faketool", tool)
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return tool


@pytest.fixture
def model_response() -> str:
    """What a model returns for `faketool`: fenced, as models often do."""
    return (FIXTURES / "faketool.1.md").read_text(encoding="utf-8")


NPM_PACKAGES = ("cowsay@1.6.0", "marked@16.4.2")


def _require(tool: str) -> str:
    found = shutil.which(tool)
    if found is None:
        pytest.fail(f"provider integration tests need `{tool}` on $PATH", pytrace=False)
    return found


def _run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=False, env=env, timeout=300
    )
    if result.returncode != 0:
        pytest.fail(
            f"`{' '.join(cmd)}` failed ({result.returncode}):\n{result.stderr[-2000:]}",
            pytrace=False,
        )


@pytest.fixture(scope="session")
def npm_prefix(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`npm install -g --prefix`: `<prefix>/bin/<tool>` -> `lib/node_modules/`."""
    prefix = tmp_path_factory.mktemp("npm-global")
    _run(
        [
            _require("npm"),
            "install",
            "--global",
            "--prefix",
            str(prefix),
            "--no-audit",
            "--no-fund",
            *NPM_PACKAGES,
        ]
    )
    return prefix
