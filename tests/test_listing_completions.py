"""`scan`'s Completion column: the login shell's source, cached per binary (ADR-0066)."""

import io
import stat
from pathlib import Path

import pytest
from rich.console import Console

from maniac.cli.listing import _list_table
from maniac.config import Config
from maniac.listing import completions
from maniac.listing.completions import NONE, completion_source
from maniac.models import Installation
from maniac.sources.completions import Generated
from maniac.sources.lookup.completions import Found
from maniac.state.models import ActionState, Candidate, PageSource, ToolRow


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(cache_dir=tmp_path / "cache")


def _candidate(tmp_path: Path, name: str = "tool", version: str = "1.0") -> Candidate:
    binary = tmp_path / "bin" / "tool"
    if not binary.exists():
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    installation = Installation(
        binary=name,
        bin_path=binary,
        real_path=binary,
        provider="fake",
        package="tool",
        version=version,
        root=tmp_path / "root",
    )
    return Candidate(tool=name, provider=None, installation=installation)


@pytest.fixture
def lookups(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every lookup that ran, answering `generated`."""
    asked: list[str] = []

    def lookup(subject: object, shell: str) -> Found:
        asked.append(shell)
        script = "#compdef tool\n"
        return Found(shell, generated=Generated(script, ("completion", shell)))

    monkeypatch.setattr(completions, "completion_for", lookup)
    return asked


def test_a_second_scan_reads_the_answer_instead_of_running_the_tool(
    tmp_path: Path, cfg: Config, lookups: list[str]
) -> None:
    candidate = _candidate(tmp_path)

    first = completion_source(candidate, "zsh", cfg)
    second = completion_source(candidate, "zsh", cfg)

    assert first == second == "generated"
    assert lookups == ["zsh"]


def test_an_upgrade_or_another_shell_asks_again(
    tmp_path: Path, cfg: Config, lookups: list[str]
) -> None:
    completion_source(_candidate(tmp_path), "zsh", cfg)
    completion_source(_candidate(tmp_path, version="2.0"), "zsh", cfg)
    completion_source(_candidate(tmp_path), "bash", cfg)

    assert lookups == ["zsh", "zsh", "bash"]


def test_two_names_for_one_file_are_two_answers(
    tmp_path: Path, cfg: Config, lookups: list[str]
) -> None:
    """A completion is named after the binary: `python` and `python3` differ."""
    completion_source(_candidate(tmp_path, "python"), "zsh", cfg)
    completion_source(_candidate(tmp_path, "python3"), "zsh", cfg)

    assert len(lookups) == 2


def test_an_unfinished_check_is_never_cached(
    tmp_path: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 4: a check that did not finish is not a "none" to remember."""
    asked: list[str] = []

    def lookup(subject: object, shell: str) -> Found:
        asked.append(shell)
        return Found(
            shell, none_because="the upstream check did not complete", complete=False
        )

    monkeypatch.setattr(completions, "completion_for", lookup)
    candidate = _candidate(tmp_path)

    assert completion_source(candidate, "zsh", cfg) == NONE
    assert completion_source(candidate, "zsh", cfg) == NONE
    assert asked == ["zsh", "zsh"]


def test_nothing_is_asked_without_a_binary(cfg: Config, lookups: list[str]) -> None:
    candidate = Candidate(tool="ghost", provider=None, installation=None)

    assert completion_source(candidate, "zsh", cfg) is None
    assert lookups == []


def _render(row: ToolRow) -> str:
    output = io.StringIO()
    Console(file=output, force_terminal=True, no_color=True, width=160).print(
        _list_table([row], terminal_width=160)
    )
    return output.getvalue()


_ROW = ToolRow(
    "tool",
    "tool",
    "fake",
    ActionState.MISSING,
    PageSource.NONE,
    None,
    completion="generated",
)


def test_the_column_shows_for_a_login_shell_with_completions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")

    rendered = _render(_ROW)

    assert "Completion" in rendered
    assert "generated" in rendered


def test_the_column_is_left_out_for_a_login_shell_without(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every answer is about the login shell; with none, there is no column."""
    monkeypatch.setenv("SHELL", "/bin/sh")

    assert "Completion" not in _render(_ROW)
