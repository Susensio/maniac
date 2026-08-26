"""Regression coverage for M12: the rendered-manpage label must name its
actual renderer, not always claim `man` produced it.
"""

import shutil
import subprocess

import pytest

from maniac.evaluation.judge import build_evaluation_prompt, render_manpage_to_terminal

MANPAGE = "% MYTOOL(1) | User Commands\n\n# NAME\nmytool - test\n"


def _stub_which(available: set[str]):
    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in available else None

    return fake_which


def _stub_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="rendered output", stderr=""
        ),
    )


def test_render_reports_man_when_groff_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", _stub_which({"pandoc", "groff"}))
    _stub_run(monkeypatch)
    assert render_manpage_to_terminal(MANPAGE) == ("rendered output", "man")


def test_render_reports_pandoc_plain_when_groff_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", _stub_which({"pandoc"}))
    _stub_run(monkeypatch)
    assert render_manpage_to_terminal(MANPAGE) == ("rendered output", "pandoc-plain")


def test_prompt_label_is_honest_without_groff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", _stub_which({"pandoc"}))
    _stub_run(monkeypatch)

    prompt = build_evaluation_prompt(
        "mytool", MANPAGE, "> mytool --help\nUsage: mytool"
    )

    assert "WHAT THE USER SEES IN `man mytool`" not in prompt
    assert "PANDOC PLAIN-TEXT FALLBACK" in prompt


def test_prompt_label_credits_man_when_groff_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", _stub_which({"pandoc", "groff"}))
    _stub_run(monkeypatch)

    prompt = build_evaluation_prompt(
        "mytool", MANPAGE, "> mytool --help\nUsage: mytool"
    )

    assert "WHAT THE USER SEES IN `man mytool`" in prompt
