"""Tests for `status` (ADR-0013/ADR-0014): decisions only, no Rich-output scraping."""

import dataclasses
from pathlib import Path

import pytest

from maniac.classification import ManpageFacts
from maniac.cli.status import StatusRow, compute_status
from maniac.models import RepoSource
from maniac.sources.manpages import Dialect

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
