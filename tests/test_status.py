"""Tests for `status` (ADR-0013/ADR-0014): decisions only, no Rich-output scraping."""

import dataclasses
import io
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

import maniac.cli as cli_module
from maniac.classification import ManpageFacts
from maniac.cli import app
from maniac.cli.status import StatusRow, _render_status, compute_status
from maniac.models import RepoSource
from maniac.sources.manpages import Dialect

runner = CliRunner()

_BASE_FACTS = ManpageFacts(
    tool="example",
    section="1",
    path=Path("/usr/share/man/man1/example.1"),
    exists=True,
    is_maniac_authored=False,
    generator=None,
    dialect=Dialect.MAN,
    word_count=0,
    tp_count=0,
    sections=[],
    has_examples_section=False,
    sources=[],
)


def _facts(**overrides: object) -> ManpageFacts:
    return dataclasses.replace(_BASE_FACTS, **overrides)


def test_compute_status_no_args_includes_source_backed_and_maniac_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_backed = _facts(
        tool="gum",
        sources=[RepoSource(name="gum", target="charmbracelet/gum", is_local=False)],
    )
    maniac_owned = _facts(tool="ty", is_maniac_authored=True, sources=[])
    unreachable = _facts(tool="bash", sources=[])

    monkeypatch.setattr(
        "maniac.cli.status.collect_facts",
        lambda: [source_backed, maniac_owned, unreachable],
    )

    rows = compute_status()
    assert {row.facts.tool for row in rows} == {"gum", "ty"}


def test_compute_status_with_tools_is_unfiltered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The argument form is the escape hatch: no source/ownership filtering applies."""
    unreachable = _facts(tool="bash", sources=[])
    other = _facts(tool="zsh", sources=[])

    monkeypatch.setattr("maniac.cli.status.collect_facts", lambda: [unreachable, other])

    rows = compute_status(["bash"])
    assert rows == [StatusRow(facts=unreachable)]


def test_compute_status_with_tools_named_but_absent_reports_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("maniac.cli.status.collect_facts", list)

    assert compute_status(["nonexistent-tool"]) == []


def test_compute_status_candidates_only_matches_real_dump_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Against ADR-0014's real numbers: selects gum/gh/pastel/just, not usage/tmux/bat/fish-lsp."""

    def _source_backed(tool: str, word_count: int, tp_count: int) -> ManpageFacts:
        return _facts(
            tool=tool,
            word_count=word_count,
            tp_count=tp_count,
            sources=[RepoSource(name=tool, target=f"org/{tool}", is_local=False)],
        )

    selected = [
        _source_backed("gum", 3805, 986),
        _source_backed("gh", 258, 33),
        _source_backed("pastel", 283, 28),
        _source_backed("just", 779, 69),
    ]
    not_selected = [
        _source_backed("usage", 2227, 123),
        _source_backed("tmux", 31902, 1017),
        _source_backed("bat", 1919, 0),
        _source_backed("fish-lsp", 826, 0),
    ]

    monkeypatch.setattr(
        "maniac.cli.status.collect_facts", lambda: selected + not_selected
    )
    monkeypatch.setattr("maniac.cli.status.default_cfg.min_words_per_flag", 15)

    rows = compute_status(candidates_only=True)
    assert {row.facts.tool for row in rows} == {"gum", "gh", "pastel", "just"}


def test_render_status_tty_shows_a_table() -> None:
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, no_color=True)

    _render_status(test_console, [StatusRow(facts=_facts(tool="gum"))])

    output = buf.getvalue()
    assert "Manpage Status" in output
    assert "gum" in output


def test_render_status_non_tty_prints_bare_names(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The path `xargs maniac generate` and `$(maniac status --candidates)` rely on."""
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=False)

    _render_status(
        test_console,
        [StatusRow(facts=_facts(tool="gum")), StatusRow(facts=_facts(tool="gh"))],
    )

    assert capsys.readouterr().out == "gum\ngh\n"
    assert buf.getvalue() == ""


def test_render_status_names_forces_bare_output_on_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True)

    _render_status(test_console, [StatusRow(facts=_facts(tool="gum"))], names=True)

    assert capsys.readouterr().out == "gum\n"
    assert buf.getvalue() == ""


def test_render_status_bare_names_dedupe_across_sections(
    capsys: pytest.CaptureFixture[str],
) -> None:
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=False)

    _render_status(
        test_console,
        [
            StatusRow(facts=_facts(tool="gum", section="1")),
            StatusRow(facts=_facts(tool="gum", section="5")),
        ],
    )

    assert capsys.readouterr().out == "gum\n"


def test_cli_status_pipe_emits_bare_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke test: `status` through the full CLI, piped, is bare names -- nothing else."""
    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=False, no_color=True)
    )
    monkeypatch.setattr(
        "maniac.cli.status.collect_facts",
        lambda: [
            _facts(
                tool="gum",
                sources=[
                    RepoSource(name="gum", target="charmbracelet/gum", is_local=False)
                ],
            )
        ],
    )

    res = runner.invoke(app, ["status"])
    assert res.exit_code == 0
    assert res.output == "gum\n"


def test_cli_status_tty_shows_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=True, no_color=True)
    )
    monkeypatch.setattr(
        "maniac.cli.status.collect_facts",
        lambda: [
            _facts(
                tool="gum",
                sources=[
                    RepoSource(name="gum", target="charmbracelet/gum", is_local=False)
                ],
            )
        ],
    )

    res = runner.invoke(app, ["status"])
    assert res.exit_code == 0
    assert "Manpage Status" in res.output
