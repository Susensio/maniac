"""Tests for `LocalLibProvider` (ADR-0015 Stage 2): detection and resolution."""

import subprocess
from pathlib import Path

from maniac.config import Config
from maniac.sources.providers import local_lib
from maniac.sources.providers.registry import registry


def _make_local_lib_tool(
    tmp_path: Path, tool: str, real_name: str
) -> tuple[Path, Path]:
    """Build `<tmp>/.local/lib/<tool>/bin/<real_name>`, return (bin_path, root)."""
    root = tmp_path / ".local" / "lib" / tool
    real = root / "bin" / real_name
    real.parent.mkdir(parents=True)
    real.touch()
    bin_path = tmp_path / ".local" / "bin" / real_name
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.symlink_to(real)
    return bin_path, root


def test_detect_fills_every_field_walking_up_to_the_checkout_root(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    bin_path, root = _make_local_lib_tool(tmp_path, "llm-functions", "run")

    inst = local_lib.LocalLibProvider().detect(bin_path)

    assert inst is not None
    assert inst.binary == "run"
    assert inst.bin_path == bin_path
    assert inst.real_path == bin_path.resolve()
    assert inst.provider == "local_lib"
    assert inst.package == "llm-functions"
    assert inst.version is None  # a checkout has git history, not a release version
    assert inst.root == root
    assert inst.parent is None


def test_detect_falls_back_to_the_immediate_parent_outside_home(
    tmp_path: Path, monkeypatch
) -> None:
    """Mirrors the prior `_resolve_local_lib`: unresolvable against `$HOME`, so it
    cannot walk up to a checkout root and settles for the binary's own directory."""
    monkeypatch.setenv("HOME", str(tmp_path / "unrelated-home"))
    bin_path, root = _make_local_lib_tool(tmp_path, "sometool", "sometool")

    inst = local_lib.LocalLibProvider().detect(bin_path)

    assert inst is not None
    assert inst.root == root / "bin"  # bin_path.resolve().parent, not the tool root


def test_detect_rejects_a_path_outside_local_lib(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    real = tmp_path / "opt" / "somewhere" / "tool"
    real.parent.mkdir(parents=True)
    real.touch()
    bin_path = tmp_path / "bin" / "tool"
    bin_path.parent.mkdir()
    bin_path.symlink_to(real)

    assert local_lib.LocalLibProvider().detect(bin_path) is None


def test_resolve_source_uses_the_git_remote_when_one_exists(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    bin_path, root = _make_local_lib_tool(tmp_path, "howdoi", "howdoi")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "remote",
            "add",
            "origin",
            "https://github.com/gleitz/howdoi.git",
        ],
        check=True,
    )
    inst = local_lib.LocalLibProvider().detect(bin_path)
    assert inst is not None

    source = local_lib.LocalLibProvider().resolve_source(
        inst, config=Config(), sources=registry
    )

    assert source is not None
    assert not source.is_local
    assert source.target == "gleitz/howdoi"


def test_resolve_source_falls_back_to_local_without_a_git_remote(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    bin_path, root = _make_local_lib_tool(tmp_path, "scratch-tool", "scratch-tool")

    inst = local_lib.LocalLibProvider().detect(bin_path)
    assert inst is not None

    source = local_lib.LocalLibProvider().resolve_source(
        inst, config=Config(), sources=registry
    )

    assert source is not None
    assert source.is_local
    assert source.local_path == root
    assert source.target == f"LOCAL:{root}"


def test_local_docs_finds_manpage_under_install_root(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    bin_path, root = _make_local_lib_tool(tmp_path, "sometool", "sometool")
    inst = local_lib.LocalLibProvider().detect(bin_path)
    assert inst is not None
    manpage = root / "sometool.1"
    manpage.touch()

    assert local_lib.LocalLibProvider().local_docs(inst) == [manpage]


def test_local_docs_finds_nothing_when_install_root_ships_no_manpage(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    bin_path, _root = _make_local_lib_tool(tmp_path, "sometool", "sometool")
    inst = local_lib.LocalLibProvider().detect(bin_path)
    assert inst is not None

    assert local_lib.LocalLibProvider().local_docs(inst) == []
