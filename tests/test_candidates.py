"""Tests for the internal candidate-selection verdict layer (ADR-0014)."""

import dataclasses
from pathlib import Path

import pytest

from maniac.candidates import CandidateSelection, select_candidate
from maniac.classification import ManpageFacts
from maniac.config import Config
from maniac.sources.manpages import Dialect

_DEFAULT_THRESHOLD = 15

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


@pytest.mark.parametrize(
    ("tool", "word_count", "tp_count", "dialect", "expected"),
    [
        ("gum", 3805, 986, Dialect.MAN, CandidateSelection.SELECTED),
        ("gh", 258, 33, Dialect.MAN, CandidateSelection.SELECTED),
        ("pastel", 283, 28, Dialect.MAN, CandidateSelection.SELECTED),
        ("just", 779, 69, Dialect.MAN, CandidateSelection.SELECTED),
        ("usage", 2227, 123, Dialect.MAN, CandidateSelection.NOT_SELECTED),
        ("tmux", 31902, 1017, Dialect.MDOC, CandidateSelection.NOT_SELECTED),
        ("zoxide", 543, 15, Dialect.MAN, CandidateSelection.NOT_SELECTED),
        ("fzf", 9263, 128, Dialect.MAN, CandidateSelection.NOT_SELECTED),
    ],
)
def test_select_candidate_against_real_dump_numbers(
    tool: str,
    word_count: int,
    tp_count: int,
    dialect: Dialect,
    expected: CandidateSelection,
) -> None:
    facts = _facts(tool=tool, word_count=word_count, tp_count=tp_count, dialect=dialect)

    assert select_candidate(facts, _DEFAULT_THRESHOLD) is expected


def test_select_candidate_boundary_usage_is_excluded_just_above_threshold() -> None:
    # usage: 2227 / 123 = 18.1, the user-confirmed boundary just outside the
    # threshold -- a future threshold change should fail this test visibly
    # rather than silently reselect usage.
    facts = _facts(tool="usage", word_count=2227, tp_count=123, dialect=Dialect.MAN)

    assert (
        select_candidate(facts, _DEFAULT_THRESHOLD) is CandidateSelection.NOT_SELECTED
    )


def test_select_candidate_boundary_aichat_excluded_by_ownership_not_score() -> None:
    # aichat: 1230 / 67 = 18.4, above the threshold on the raw measure too,
    # but ownership is checked first -- MANIAC-authored is decisive
    # regardless of where the score falls.
    facts = _facts(
        tool="aichat",
        word_count=1230,
        tp_count=67,
        dialect=Dialect.MAN,
        is_maniac_authored=True,
    )

    assert (
        select_candidate(facts, _DEFAULT_THRESHOLD) is CandidateSelection.NOT_SELECTED
    )


@pytest.mark.parametrize(
    ("tool", "word_count", "tp_count"),
    [
        ("bat", 1919, 0),
        ("fish-lsp", 826, 0),
    ],
)
def test_select_candidate_zero_flag_entries_is_no_evidence(
    tool: str, word_count: int, tp_count: int
) -> None:
    facts = _facts(
        tool=tool, word_count=word_count, tp_count=tp_count, dialect=Dialect.MAN
    )

    assert select_candidate(facts, _DEFAULT_THRESHOLD) is CandidateSelection.NO_EVIDENCE


def test_select_candidate_unknown_dialect_is_no_evidence() -> None:
    facts = _facts(word_count=100, tp_count=5, dialect=Dialect.UNKNOWN)

    assert select_candidate(facts, _DEFAULT_THRESHOLD) is CandidateSelection.NO_EVIDENCE


def test_select_candidate_missing_page_is_selected_regardless_of_other_facts() -> None:
    facts = _facts(
        exists=False,
        is_maniac_authored=True,
        dialect=Dialect.UNKNOWN,
        word_count=0,
        tp_count=0,
    )

    assert select_candidate(facts, _DEFAULT_THRESHOLD) is CandidateSelection.SELECTED


def test_select_candidate_maniac_authored_wins_over_missing_evidence() -> None:
    facts = _facts(is_maniac_authored=True, dialect=Dialect.UNKNOWN, tp_count=0)

    assert (
        select_candidate(facts, _DEFAULT_THRESHOLD) is CandidateSelection.NOT_SELECTED
    )


def test_select_candidate_does_not_store_result_on_facts() -> None:
    facts = _facts(word_count=100, tp_count=50)

    select_candidate(facts, _DEFAULT_THRESHOLD)

    assert not hasattr(facts, "selection")
    assert not hasattr(facts, "candidate")


def test_min_words_per_flag_default_is_fifteen() -> None:
    assert Config().min_words_per_flag == 15


def test_min_words_per_flag_reads_config_toml_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "config.toml").write_text(
        "[classification]\nmin_words_per_flag = 40\n", encoding="utf-8"
    )
    from maniac.config import _load_config_file

    monkeypatch.setattr("maniac.config._CONFIG_VALUES", _load_config_file(tmp_path))

    assert Config().min_words_per_flag == 40


def test_config_override_changes_which_tools_are_selected() -> None:
    # zoxide: 543 / 15 = 36.2 -- not selected at the packaged default (15),
    # selected once the threshold is raised past its score.
    facts = _facts(tool="zoxide", word_count=543, tp_count=15, dialect=Dialect.MAN)

    assert select_candidate(facts, 15) is CandidateSelection.NOT_SELECTED
    assert select_candidate(facts, 40) is CandidateSelection.SELECTED
