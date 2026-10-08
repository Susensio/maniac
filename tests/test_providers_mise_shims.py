"""Tests for resolving Mise shims to the binary they run from `$HOME` (ADR-0063).

The layout mirrors what real mise produced (2026.10.4, npm backend): a shim
is a symlink to the `mise` executable in `<data>/mise/shims/`, and
`mise bin-paths` lists each globally active tool's bin directory.
"""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from maniac.config import Config
from maniac.exceptions import MalformedToolMetadata, NotGloballySelected
from maniac.orchestration.context import resolve_tool
from maniac.sources import resolution
from maniac.sources.providers import mise

_DATA = Path(".local") / "share" / "mise"


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _mise_binary(tmp_path: Path) -> Path:
    return _executable(tmp_path / "opt" / "mise" / "bin" / "mise")


def _shim(tmp_path: Path, name: str, mise_bin: Path) -> Path:
    shim = tmp_path / _DATA / "shims" / name
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.symlink_to(mise_bin)
    return shim


def _npm_install(tmp_path: Path, package: str, version: str, binary: str) -> Path:
    """`installs/npm-<pkg>/<version>/node_modules/.bin/<binary>` -> the package's script."""
    root = tmp_path / _DATA / "installs" / f"npm-{package}" / version
    script = _executable(root / "node_modules" / package / "cli.js")
    bin_dir = root / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / binary).symlink_to(script)
    return bin_dir


class _FakeMise:
    """Answers `mise bin-paths` and `mise ls --current --json`, recording calls."""

    def __init__(self, bin_paths: list[Path], current: dict[str, list[dict]]) -> None:
        self.bin_paths = bin_paths
        self.current = current
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(
        self, cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((cmd, kwargs))
        if cmd[1:] == ["bin-paths"]:
            out = "\n".join(str(p) for p in self.bin_paths) + "\n"
        elif cmd[1:] == ["ls", "--current", "--json"]:
            out = json.dumps(self.current)
        else:
            raise AssertionError(f"unexpected mise call {cmd}")
        return subprocess.CompletedProcess(cmd, 0, stdout=out)


def _use(monkeypatch: pytest.MonkeyPatch, fake: _FakeMise) -> None:
    monkeypatch.setattr(mise.discovery.shutil, "which", lambda name: "/usr/bin/mise")
    monkeypatch.setattr(mise.discovery.subprocess, "run", fake)


def test_a_path_entry_that_is_not_a_shim_is_left_alone(tmp_path: Path) -> None:
    """Neither a `shims/` entry pointing elsewhere nor the `mise` binary
    itself outside `shims/` is a shim -- and nothing asks mise."""
    elsewhere = _executable(tmp_path / "real" / "tool")
    not_mise = tmp_path / _DATA / "shims" / "tool"
    not_mise.parent.mkdir(parents=True)
    not_mise.symlink_to(elsewhere)

    assert mise.shim_target(not_mise) is None
    assert mise.shim_target(_mise_binary(tmp_path)) is None


def test_a_shim_resolves_to_the_first_global_bin_dir_holding_its_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Asked once, from `$HOME`, with activation state scrubbed -- the same
    `$HOME` scope ADR-0061 judges global selection in."""
    shim = _shim(tmp_path, "cowsay", _mise_binary(tmp_path))
    other = _npm_install(tmp_path, "other", "1.0.0", "other")
    first = _npm_install(tmp_path, "cowsay", "1.5.0", "cowsay")
    second = tmp_path / "later" / "bin"
    _executable(second / "cowsay")
    fake = _FakeMise([other, first, second], {})
    _use(monkeypatch, fake)
    monkeypatch.setenv("__MISE_DIFF", "activation")
    monkeypatch.setenv("MISE_CONFIG_DIR", "/config/mise")

    assert mise.shim_target(shim) == first / "cowsay"
    assert mise.shim_target(shim) == first / "cowsay"

    assert [cmd for cmd, _ in fake.calls] == [["/usr/bin/mise", "bin-paths"]]
    kwargs = fake.calls[0][1]
    assert kwargs["cwd"] == Path.home()
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert "__MISE_DIFF" not in env
    assert env["MISE_CONFIG_DIR"] == "/config/mise"


def test_a_shim_no_global_tool_provides_is_not_globally_selected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mise writes shims for project-only installs too; from `$HOME` there
    is nothing for this one to run."""
    shim = _shim(tmp_path, "cowsay", _mise_binary(tmp_path))
    _use(monkeypatch, _FakeMise([], {}))

    with pytest.raises(NotGloballySelected) as raised:
        mise.shim_target(shim)
    assert raised.value.tool == "cowsay"
    assert raised.value.path == shim


def test_mise_failing_behind_a_shim_is_malformed_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shim = _shim(tmp_path, "cowsay", _mise_binary(tmp_path))
    monkeypatch.setattr(mise.discovery.shutil, "which", lambda name: "/usr/bin/mise")
    monkeypatch.setattr(
        mise.discovery.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout=""),
    )

    with pytest.raises(MalformedToolMetadata):
        mise.shim_target(shim)


def test_a_shimmed_tool_is_claimed_as_its_global_mise_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End to end through `find_installation`: the shim on the login `$PATH`
    becomes the global 1.5.0 install -- provider, version and root from the
    install tree -- and that install's file, not the shim, is what runs."""
    shim = _shim(tmp_path, "cowsay", _mise_binary(tmp_path))
    global_bin = _npm_install(tmp_path, "cowsay", "1.5.0", "cowsay")
    _npm_install(tmp_path, "cowsay", "1.6.0", "cowsay")
    root = global_bin.parent.parent
    _use(
        monkeypatch,
        _FakeMise([global_bin], {"npm:cowsay": [{"install_path": str(root)}]}),
    )
    monkeypatch.setenv("PATH", str(shim.parent))

    found = resolution.find_installation("cowsay")

    assert found is not None
    provider, inst = found
    assert provider.name == "mise"
    assert (inst.package, inst.version, inst.root) == ("npm-cowsay", "1.5.0", root)
    assert inst.bin_path == global_bin / "cowsay"
    assert resolve_tool("cowsay", config=Config()).executable == str(
        global_bin / "cowsay"
    )


def test_shims_of_different_tools_stay_different_binaries_in_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every shim resolves to the same `mise` file; once swapped for its
    target, two shimmed tools no longer share one `real_path` (which `list`
    groups rows by, ADR-0049)."""
    mise_bin = _mise_binary(tmp_path)
    _shim(tmp_path, "cowsay", mise_bin)
    shim_dir = _shim(tmp_path, "other", mise_bin).parent
    cowsay_bin = _npm_install(tmp_path, "cowsay", "1.5.0", "cowsay")
    other_bin = _npm_install(tmp_path, "other", "2.0.0", "other")
    _use(
        monkeypatch,
        _FakeMise(
            [cowsay_bin, other_bin],
            {
                "npm:cowsay": [{"install_path": str(cowsay_bin.parent.parent)}],
                "npm:other": [{"install_path": str(other_bin.parent.parent)}],
            },
        ),
    )
    monkeypatch.setenv("PATH", str(shim_dir))

    claims = {inst.binary: inst for _, inst in resolution.enumerate_installations()}

    assert set(claims) == {"cowsay", "other"}
    assert claims["cowsay"].real_path != claims["other"].real_path
    assert claims["cowsay"].version == "1.5.0"
    assert claims["other"].version == "2.0.0"


@pytest.mark.skipif(os.name != "posix", reason="symlink shims are POSIX mise")
def test_a_session_pinned_shim_target_is_still_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`mise shell tool@v` survives the `$HOME` scrub (ADR-0061 Corrections),
    so `bin-paths` can point at the session's version; the install's own
    global-selection check, which drops environment-sourced entries, still
    refuses it."""
    shim = _shim(tmp_path, "cowsay", _mise_binary(tmp_path))
    session_bin = _npm_install(tmp_path, "cowsay", "1.6.0", "cowsay")
    session_root = session_bin.parent.parent
    _use(
        monkeypatch,
        _FakeMise(
            [session_bin],
            {
                "npm:cowsay": [
                    {
                        "install_path": str(session_root),
                        "source": {"type": "environment"},
                    }
                ]
            },
        ),
    )
    monkeypatch.setenv("PATH", str(shim.parent))

    with pytest.raises(NotGloballySelected):
        resolution.find_installation("cowsay")
