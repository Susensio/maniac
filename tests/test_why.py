"""`why`'s report for the cases integration tests cannot set up cheaply."""

import os
import stat
from pathlib import Path

import pytest

from maniac.config import Config
from maniac.exceptions import NotGloballySelected
from maniac.models import RemoteRepoSource
from maniac.orchestration.why import explain
from maniac.sources import pathcache
from maniac.sources.candidates import RepositoryCandidate

from .listing_support import _FakeProvider, _installation


def _lines(tool: str, title: str) -> list[str]:
    explanation = explain(tool, Config())
    return next(s.lines for s in explanation.sections if s.title == title)


def _script(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho '{version}'\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def no_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "maniac.orchestration.why.find_installed_manpage_path", lambda man, tool: None
    )


def test_a_project_only_mise_install_is_refused_and_offers_force(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_page: None
) -> None:
    hit = _script(tmp_path / "mise" / "installs" / "rg" / "13" / "rg", "rg 13")
    here = _script(tmp_path / "proj" / "bin" / "rg", "rg 13")
    monkeypatch.setattr(pathcache, "which", lambda name: hit)
    monkeypatch.setenv("PATH", str(here.parent))

    def refused(name: str, bin_dir: object = None, **_: object) -> None:
        raise NotGloballySelected(name, hit.parent)

    monkeypatch.setattr("maniac.orchestration.why.find_installation", refused)

    explanation = explain("rg", Config())
    binary = next(s.lines for s in explanation.sections if s.title == "Binary")

    assert binary[-1] == f"this shell runs {here}; `--force` documents that copy"
    assert explanation.verdict == "refused: not installed globally"


def test_a_system_binary_is_named_as_one(
    monkeypatch: pytest.MonkeyPatch, no_page: None
) -> None:
    monkeypatch.setattr(pathcache, "which", lambda name: Path("/usr/bin/true"))
    monkeypatch.setattr(
        "maniac.orchestration.why.find_installation", lambda name, **_: None
    )

    explanation = explain("true", Config())

    assert explanation.verdict == "refused: a system package's binary"
    assert any(
        "a system package's binary: maniac leaves it to its package; `--force` installs anyway"
        == line
        for line in next(s.lines for s in explanation.sections if s.title == "Binary")
    )


@pytest.mark.parametrize(
    ("found", "definitive", "expected", "verdict"),
    [
        (True, True, "upstream: o/tool at 1.2.3: tool.1", "upstream"),
        (False, True, "upstream: o/tool at 1.2.3: no manpage", "generated"),
        (
            False,
            False,
            "upstream: o/tool at 1.2.3: the check failed (network?), so install refuses to generate",
            "refused: the upstream check did not complete",
        ),
    ],
)
def test_the_upstream_check_is_reported_as_install_would_act_on_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    no_page: None,
    found: bool,
    definitive: bool,
    expected: str,
    verdict: str,
) -> None:
    """Including the case `install` refuses: a check that did not complete."""
    tool = _script(tmp_path / "bin" / "tool", "tool 1.2.3")
    source = RemoteRepoSource.from_identifier("tool", "o/tool")
    assert source is not None
    provider = _FakeProvider(source=source)
    inst = _installation(binary="tool", version="1.2.3", root=tmp_path)
    monkeypatch.setattr(pathcache, "which", lambda name: tool)
    monkeypatch.setenv("PATH", os.pathsep.join([str(tool.parent)]))
    monkeypatch.setattr(
        "maniac.orchestration.why.find_installation", lambda name, **_: (provider, inst)
    )
    monkeypatch.setattr(
        "maniac.orchestration.why.select_install_root", lambda provider, inst: None
    )
    monkeypatch.setattr(
        "maniac.orchestration.why.registry.resolve_source",
        lambda inst, config, provider: source,
    )
    page = tmp_path / "tool.1"

    def select_repository(*args: object, **kwargs: object):
        if not found:
            return None, definitive
        from maniac.sources.candidates import CandidatePage

        candidate_page = CandidatePage(page, page.as_uri())
        return (
            RepositoryCandidate(
                source=source,
                pages=(candidate_page,),
                primary=candidate_page,
                version_matched=True,
            ),
            definitive,
        )

    monkeypatch.setattr("maniac.orchestration.why.select_repository", select_repository)

    explanation = explain("tool", Config())
    sources = next(s.lines for s in explanation.sections if s.title == "Sources")

    assert expected in sources
    assert explanation.verdict == verdict


def test_path_names_only_left_out_directories_holding_the_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_page: None
) -> None:
    login = tmp_path / "login"
    tool = _script(login / "tool", "tool 1")
    venv = tmp_path / ".venv" / "bin"
    _script(venv / "tool", "tool 2")
    unrelated = [tmp_path / "games", tmp_path / "snap"]
    for directory in unrelated:
        directory.mkdir()
    monkeypatch.setattr(pathcache, "_spawn_login_shell", lambda: str(login))
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(venv), str(login), *map(str, unrelated)])
    )
    monkeypatch.setenv("VIRTUAL_ENV", str(venv.parent))
    monkeypatch.setattr(
        "maniac.orchestration.why.find_installation", lambda n, **_: None
    )
    assert pathcache.which("tool") == tool

    lines = _lines("tool", "PATH")

    assert lines[1:] == [
        f"left out: {venv}, an activated virtualenv (VIRTUAL_ENV) (holds a copy of this tool)",
        "2 more this shell has are left out; none holds this tool",
    ]
