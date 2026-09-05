import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from maniac.cli import app
from maniac.evaluation import (
    build_comparison_prompt,
    compare_manpages,
    parse_comparison_json,
    run_comparison_judge,
)
from maniac.models import ComparisonResult, EvaluationResult

runner = CliRunner()

GENERATED_MANPAGE = """% TOOL(1) | User Commands

# NAME
tool - a demonstration command-line tool

# SYNOPSIS
**tool** [*OPTIONS*] <*COMMAND*>

# DESCRIPTION
**tool** manages distributed components.

# COMMANDS
**tool run** [*args*]
:   Execute workspace task.

# OPTIONS
**-v**, **--verbose**
:   Enable verbose mode.

# EXIT STATUS
0
:   Success.

# EXAMPLES
```bash
tool run build
```
"""

INSTALLED_MANPAGE = (
    '.TH TOOL 1 "2024" "tool 1.0" "User Commands"\n'
    ".SH NAME\n"
    "tool \\- a demo tool\n"
    ".SH SYNOPSIS\n"
    ".B tool\n"
    "[options]\n"
)


def test_build_comparison_prompt_includes_both_pages() -> None:
    prompt = build_comparison_prompt(
        "tool", INSTALLED_MANPAGE, GENERATED_MANPAGE, "context text"
    )
    assert "MANUAL PAGE A -- INSTALLED" in prompt
    assert "MANUAL PAGE B -- MANIAC-GENERATED" in prompt
    assert "=== REFERENCE CONTEXT FOR 'tool' ===" in prompt
    assert "context text" in prompt


def test_parse_comparison_json_clean() -> None:
    raw = json.dumps(
        {
            "winner": "generated",
            "differences": "Generated covers more subcommands.",
            "installed_strengths": ["Ships with the package"],
            "generated_strengths": ["Documents all subcommands"],
        }
    )
    data = parse_comparison_json(raw)
    assert data["winner"] == "generated"
    assert data["differences"] == "Generated covers more subcommands."
    assert data["installed_strengths"] == ["Ships with the package"]
    assert data["generated_strengths"] == ["Documents all subcommands"]


def test_parse_comparison_json_invalid_winner_defaults_to_tie() -> None:
    raw = json.dumps({"winner": "nonsense", "differences": "x"})
    data = parse_comparison_json(raw)
    assert data["winner"] == "tie"


def test_parse_comparison_json_code_fence() -> None:
    raw = """```json
    {"winner": "installed", "differences": "Installed is terser.",
     "installed_strengths": [], "generated_strengths": []}
    ```"""
    data = parse_comparison_json(raw)
    assert data["winner"] == "installed"
    assert data["differences"] == "Installed is terser."


def test_parse_comparison_json_malformed() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_comparison_json("not valid json")


def test_run_comparison_judge_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "maniac.evaluation.judge.run_llm_synthesis",
        lambda *a, **kw: json.dumps(
            {
                "winner": "tie",
                "differences": "Both cover the basics.",
                "installed_strengths": [],
                "generated_strengths": [],
            }
        ),
    )
    result = run_comparison_judge(
        "tool", INSTALLED_MANPAGE, GENERATED_MANPAGE, "context text"
    )
    assert result["winner"] == "tie"
    assert result["differences"] == "Both cover the basics."


def test_run_comparison_judge_malformed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "maniac.evaluation.judge.run_llm_synthesis", lambda *a, **kw: "not json"
    )
    result = run_comparison_judge(
        "tool", INSTALLED_MANPAGE, GENERATED_MANPAGE, "context text"
    )
    assert result["winner"] == "tie"
    assert "malformed" in result["differences"].lower()


def test_compare_manpages_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)

    responses = iter(
        [
            json.dumps(
                {
                    "score": 92,
                    "passed": True,
                    "rubric_breakdown": {
                        "domain_ontology": 18,
                        "correctness_coverage": 19,
                        "formatting": 18,
                        "subsystem_grouping": 18,
                        "environment_reference_examples": 19,
                    },
                    "defects": [],
                    "summary": "Great manual.",
                }
            ),
            json.dumps(
                {
                    "score": 60,
                    "passed": False,
                    "rubric_breakdown": {
                        "domain_ontology": 12,
                        "correctness_coverage": 12,
                        "formatting": 12,
                        "subsystem_grouping": 12,
                        "environment_reference_examples": 12,
                    },
                    "defects": ["Terse."],
                    "summary": "Adequate.",
                }
            ),
            json.dumps(
                {
                    "winner": "generated",
                    "differences": "Generated documents more subcommands and includes examples.",
                    "installed_strengths": ["Concise"],
                    "generated_strengths": [
                        "Covers all subcommands",
                        "Includes examples",
                    ],
                }
            ),
        ]
    )

    def fake_synthesis(*args: Any, **kwargs: Any) -> str:
        return next(responses)

    monkeypatch.setattr("maniac.evaluation.judge.run_llm_synthesis", fake_synthesis)

    result = compare_manpages(
        "tool", GENERATED_MANPAGE, INSTALLED_MANPAGE, "context text"
    )
    assert isinstance(result, ComparisonResult)
    assert result.generated.score == 92
    assert result.installed.score == 60
    assert result.winner == "generated"
    assert "subcommands" in result.differences
    assert result.installed_strengths == ["Concise"]
    assert result.generated_strengths == ["Covers all subcommands", "Includes examples"]


def test_compute_compare_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from maniac.cli.evaluate import compute_compare

    manpage_path = tmp_path / "tool.1.md"
    manpage_path.write_text(GENERATED_MANPAGE, encoding="utf-8")
    context_path = tmp_path / "tool_context.md"
    context_path.write_text("Context documentation", encoding="utf-8")
    installed_path = Path("/usr/share/man/man1/tool.1.gz")

    monkeypatch.setattr(
        "maniac.sources.manpages.find_installed_manpage_path",
        lambda man_bin, tool_name: installed_path,
    )
    monkeypatch.setattr(
        "maniac.sources.manpages.read_manpage_source",
        lambda path: INSTALLED_MANPAGE,
    )
    expected = ComparisonResult(
        tool_name="tool",
        installed=EvaluationResult(score=60, passed=False, rubric_breakdown={}),
        generated=EvaluationResult(score=90, passed=True, rubric_breakdown={}),
        winner="generated",
        differences="Generated is more thorough.",
        installed_strengths=["Concise"],
        generated_strengths=["Covers all subcommands"],
    )
    monkeypatch.setattr(
        "maniac.evaluation.judge.compare_manpages", lambda **kw: expected
    )

    outcome = compute_compare("tool", manpage_path, context_path)
    assert outcome.error is None
    assert outcome.result is expected
    assert outcome.installed_path == installed_path


def test_compute_compare_no_installed_manpage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from maniac.cli.evaluate import compute_compare

    manpage_path = tmp_path / "tool.1.md"
    manpage_path.write_text(GENERATED_MANPAGE, encoding="utf-8")
    context_path = tmp_path / "tool_context.md"
    context_path.write_text("Context documentation", encoding="utf-8")

    monkeypatch.setattr(
        "maniac.sources.manpages.find_installed_manpage_path",
        lambda man_bin, tool_name: None,
    )

    outcome = compute_compare("tool", manpage_path, context_path)
    assert outcome.result is None
    assert outcome.error is not None
    assert "No manpage is currently installed" in outcome.error


def test_compute_compare_missing_generated_manpage(tmp_path: Path) -> None:
    from maniac.cli.evaluate import compute_compare

    context_path = tmp_path / "tool_context.md"
    context_path.write_text("Context documentation", encoding="utf-8")

    outcome = compute_compare("nonexistent_binary_xyz_123", context_file=context_path)
    assert outcome.result is None
    assert outcome.error is not None
    assert "Generated manpage not found" in outcome.error


def test_cli_compare_smoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Rendering smoke test: the wiring from outcome to console output, exit code included."""
    manpage_path = tmp_path / "tool.1.md"
    manpage_path.write_text(GENERATED_MANPAGE, encoding="utf-8")
    context_path = tmp_path / "tool_context.md"
    context_path.write_text("Context documentation", encoding="utf-8")

    monkeypatch.setattr(
        "maniac.sources.manpages.find_installed_manpage_path",
        lambda man_bin, tool_name: Path("/usr/share/man/man1/tool.1.gz"),
    )
    monkeypatch.setattr(
        "maniac.sources.manpages.read_manpage_source",
        lambda path: INSTALLED_MANPAGE,
    )
    monkeypatch.setattr(
        "maniac.evaluation.judge.compare_manpages",
        lambda **kw: ComparisonResult(
            tool_name="tool",
            installed=EvaluationResult(score=60, passed=False, rubric_breakdown={}),
            generated=EvaluationResult(score=90, passed=True, rubric_breakdown={}),
            winner="generated",
            differences="Generated is more thorough.",
            installed_strengths=["Concise"],
            generated_strengths=["Covers all subcommands"],
        ),
    )

    res = runner.invoke(
        app,
        [
            "compare",
            "tool",
            "--manpage-file",
            str(manpage_path),
            "--context-file",
            str(context_path),
        ],
    )
    assert res.exit_code == 0
    assert "Generated is more thorough." in res.output
    assert "MANIAC-generated" in res.output
