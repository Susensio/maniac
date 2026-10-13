"""A setup that builds `$PATH` only in an interactive rc file (CONTRACT.md rule 2).

Mise's front-page setup puts `mise activate` in `.bashrc`: the invoking
shell reaches each global tool's install directory, the non-interactive
login shell maniac resolves through never does. maniac still documents only
what the login `$PATH` reaches, but tells this setup apart from a machine
without the tool: the copy this shell runs is one its installer calls
global. It says so, with the fix, in `install`, `why` and `scan`.
"""

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from maniac.config import Config
from maniac.exceptions import OffLoginPath
from maniac.listing.inventory import Skip, _skip_reason
from maniac.orchestration.install import InstallRefused, run_install
from maniac.orchestration.why import explain
from maniac.sources import pathcache
from maniac.sources.lookup import resolution
from maniac.sources.providers import mise
from maniac.state.candidates import named_candidate

_INSTALLS = Path(".local") / "share" / "mise" / "installs"


def _install(tmp_path: Path, tool: str, version: str) -> Path:
    """A mise install's bin directory, as `mise activate` puts it on `$PATH`."""
    bin_dir = tmp_path / _INSTALLS / tool / version / "bin"
    bin_dir.mkdir(parents=True)
    binary = bin_dir / tool
    binary.write_text(f"#!/bin/sh\necho '{tool} {version}'\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return bin_dir


@pytest.fixture
def rc_file_setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    """`cowsay` is selected globally (`mise use -g`), `projtool` only by a
    project; `.bashrc` activated both, the login profile neither."""
    login = tmp_path / "login"
    login.mkdir()
    cowsay = _install(tmp_path, "cowsay", "1.5.0")
    projtool = _install(tmp_path, "projtool", "2.0.0")
    current = {"cowsay": [{"install_path": str(cowsay.parent)}]}

    real_run = subprocess.run

    def fake_mise(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        # `subprocess.run` is one function everywhere: pass the rest (`man`) on.
        if cmd[0] != "/usr/bin/mise":
            return real_run(cmd, **kwargs)
        answers: dict[tuple[str, ...], object] = {
            ("ls", "--current", "--json"): current,
            ("config", "ls", "--json"): [],
        }
        assert tuple(cmd[1:]) in answers, cmd
        out = json.dumps(answers[tuple(cmd[1:])])
        return subprocess.CompletedProcess(cmd, 0, stdout=out)

    monkeypatch.setattr(mise.discovery.shutil, "which", lambda name: "/usr/bin/mise")
    monkeypatch.setattr(mise.discovery.subprocess, "run", fake_mise)
    monkeypatch.setattr(pathcache, "_spawn_login_shell", lambda: str(login))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(cowsay), str(projtool), str(login)])
    )
    return {"cowsay": cowsay / "cowsay", "projtool": projtool / "projtool"}


def test_a_global_install_only_this_shell_reaches_is_told_apart(
    rc_file_setup: dict[str, Path],
) -> None:
    """Off the login `$PATH`, but its installer calls it global: not "not
    installed", and still not what a page documents."""
    located = resolution.locate("cowsay")

    assert located.outcome is resolution.Outcome.OFF_LOGIN_PATH
    assert located.binary == rc_file_setup["cowsay"]
    assert located.provider is not None and located.provider.name == "mise"
    assert isinstance(located.error, OffLoginPath)


def test_a_project_only_install_stays_not_on_path(
    rc_file_setup: dict[str, Path],
) -> None:
    """A copy only a project selects is no global install the profile missed."""
    assert resolution.locate("projtool").outcome is resolution.Outcome.NOT_ON_PATH


def test_scan_counts_it_and_never_shows_it_as_a_tool(
    rc_file_setup: dict[str, Path],
) -> None:
    found = {located.tool: located for located in resolution.locate_all()}

    assert _skip_reason(found["cowsay"]) is Skip.OFF_LOGIN_PATH
    assert "projtool" not in found
    assert named_candidate("cowsay").error is not None


def test_install_refuses_with_the_reason_and_the_fix(
    rc_file_setup: dict[str, Path],
) -> None:
    with pytest.raises(InstallRefused) as raised:
        run_install("cowsay", config=Config())

    message = str(raised.value)
    assert "install it globally first" not in message.lower()
    assert "mise installed it globally" in message
    assert 'Add `eval "$(mise activate bash --shims)"` to ~/.profile.' in message
    assert "`--force` documents that copy" in message


def test_why_names_the_rc_file_not_a_project(
    rc_file_setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "maniac.orchestration.why.find_installed_manpage_path", lambda man, tool: None
    )
    explanation = explain("cowsay", Config())
    path = next(s.lines for s in explanation.sections if s.title == "PATH")
    binary = next(s.lines for s in explanation.sections if s.title == "Binary")

    assert not any("for this directory" in line for line in path)
    assert any("mise installed it globally" in line for line in binary)
    assert any("mise activate bash --shims" in line for line in binary)


def test_list_says_why_a_page_cannot_be_checked(
    rc_file_setup: dict[str, Path], tmp_path: Path
) -> None:
    """A page maniac installed whose tool the login `$PATH` now misses:
    `unknown`, with the reason, not "the installed version cannot be read"."""
    from maniac.manifest import Entry, Tier
    from maniac.state.models import ActionState
    from maniac.state.pages import managed_pages

    from .manifest_support import record_entry

    cfg = Config(
        man_dir=tmp_path / "man" / "man1",
        output_dir=tmp_path / "out",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    cfg.output_dir.mkdir(parents=True)
    cfg.man_dir.mkdir(parents=True)
    target = cfg.output_dir / "cowsay.1"
    target.write_text(".TH X 1\n", encoding="utf-8")
    (cfg.man_dir / "cowsay.1").symlink_to(target)
    record_entry(
        "cowsay",
        Entry(
            path=cfg.man_dir / "cowsay.1",
            tier=Tier.SYNTHESIS,
            source="model",
            checksum="c",
            version="cowsay 1.5.0",
            target=target,
        ),
        config=cfg,
    )

    [row], _ = managed_pages(cfg)

    assert row.state is ActionState.UNKNOWN
    assert row.note == "installed globally, not on your login $PATH"


def test_a_virtualenv_python_linking_to_a_global_install_is_not_reported(
    rc_file_setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A venv's `python` resolves into the global interpreter it was made from;
    the venv is still a project's, whether activated or put there by direnv."""
    venv_bin = tmp_path / "proj" / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin.parent / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    (venv_bin / "cowsay").symlink_to(rc_file_setup["cowsay"])
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(venv_bin), str(tmp_path / "login")])
    )

    assert resolution.locate("cowsay").outcome is resolution.Outcome.NOT_ON_PATH
    assert "cowsay" not in {located.tool for located in resolution.locate_all()}
