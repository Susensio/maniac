import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from maniac.cli import app
from maniac.evaluation import (
    DeterministicCheck,
    LLMJudge,
    evaluate_manpage,
    parse_evaluation_json,
)
from maniac.models import EvaluationResult

runner = CliRunner()

VALID_MANPAGE = """% TOOL(1) | User Commands

# NAME
tool - a demonstration command-line tool

# SYNOPSIS
**tool** [*OPTIONS*] <*COMMAND*>

# DESCRIPTION
**tool** manages distributed components.

**Workspace**
:   An isolated environment.

# COMMANDS

## Primary Operations
**tool run** [*args*]
:   Execute workspace task.

# OPTIONS

## General
**-v**, **--verbose**
:   Enable verbose mode.

# ENVIRONMENT
*TOOL_CONFIG*
:   Path to configuration file.

# FILES
`~/.config/tool.toml`
:   Default configuration.

# EXIT STATUS
0
:   Success.

1
:   General failure.

# EXAMPLES
Run a workspace build:
```bash
# Execute compilation within isolated workspace
tool run build
```

# SEE ALSO
**sh**(1)
"""


def test_deterministic_check_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pandoc")

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout=".TH TOOL 1", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    passed, defects = DeterministicCheck.run(VALID_MANPAGE, tool_name="tool")
    assert passed
    assert len(defects) == 0


def test_deterministic_check_missing_header() -> None:
    bad_md = "# NAME\ntool - bad header\n# SYNOPSIS\ntool"
    passed, defects = DeterministicCheck.run(bad_md, tool_name="tool")
    assert not passed
    assert any("First line must start with '% '" in d for d in defects)


def test_deterministic_check_mismatched_tool_name() -> None:
    passed, defects = DeterministicCheck.run(VALID_MANPAGE, tool_name="othertool")
    assert not passed
    assert any("does not match expected tool 'OTHERTOOL'" in d for d in defects)


def test_deterministic_check_missing_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pandoc")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            args=a[0], returncode=0, stdout="", stderr=""
        ),
    )

    bad_md = "% TOOL(1) | User Commands\n\n# NAME\ntool\n# SYNOPSIS\ntool\n# DESCRIPTION\ntool"
    passed, defects = DeterministicCheck.run(bad_md, tool_name="tool")
    assert not passed
    assert any("Missing required section: #EXIT STATUS" in d for d in defects)
    assert any("Missing required section: #EXAMPLES" in d for d in defects)
    assert any(
        "Missing required section: at least one of #OPTIONS or #COMMANDS" in d
        for d in defects
    )


def test_deterministic_check_pandoc_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pandoc")

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="Pandoc syntax error"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    passed, defects = DeterministicCheck.run(VALID_MANPAGE, tool_name="tool")
    assert not passed
    assert any("Pandoc compilation failed: Pandoc syntax error" in d for d in defects)


def test_deterministic_check_no_pandoc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    passed, defects = DeterministicCheck.run(VALID_MANPAGE, tool_name="tool")
    assert not passed
    assert any("pandoc is not installed" in d for d in defects)


def test_parse_evaluation_json_clean() -> None:
    raw = json.dumps(
        {
            "score": 95,
            "passed": True,
            "rubric_breakdown": {
                "domain_ontology": 19,
                "formatting": 20,
                "subsystem_grouping": 18,
                "environment_files_exit": 19,
                "workflow_examples": 19,
            },
            "defects": [],
            "summary": "Excellent manual.",
        }
    )
    result = parse_evaluation_json(raw)
    assert result.score == 95
    assert result.passed is True
    assert result.rubric_breakdown["domain_ontology"] == 19
    assert result.summary == "Excellent manual."


def test_parse_evaluation_json_code_fence() -> None:
    raw = """```json
    {
      "score": 85,
      "passed": true,
      "rubric_breakdown": {
        "domain_ontology": 17,
        "formatting": 18,
        "subsystem_grouping": 16,
        "environment_files_exit": 17,
        "workflow_examples": 17
      },
      "defects": ["Minor note"],
      "summary": "Good."
    }
    ```"""
    result = parse_evaluation_json(raw)
    assert result.score == 85
    assert result.passed is True
    assert result.defects == ["Minor note"]


def test_parse_evaluation_json_with_header() -> None:
    raw = """% TOOL(1) | User Commands

    {
      "score": 75,
      "passed": true,
      "rubric_breakdown": {
        "domain_ontology": 15,
        "formatting": 15,
        "subsystem_grouping": 15,
        "environment_files_exit": 15,
        "workflow_examples": 15
      },
      "defects": [],
      "summary": "Passable."
    }"""
    result = parse_evaluation_json(raw)
    assert result.score == 75
    assert result.passed is True


def test_parse_evaluation_json_malformed() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_evaluation_json("not valid json")


def test_llm_judge_evaluate_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_synthesis(*args: Any, **kwargs: Any) -> str:
        return json.dumps(
            {
                "score": 90,
                "passed": True,
                "rubric_breakdown": {
                    "domain_ontology": 18,
                    "formatting": 18,
                    "subsystem_grouping": 18,
                    "environment_files_exit": 18,
                    "workflow_examples": 18,
                },
                "defects": [],
                "summary": "High quality.",
            }
        )

    monkeypatch.setattr("maniac.evaluation.judge.run_llm_synthesis", fake_synthesis)

    judge = LLMJudge(pass_threshold=70)
    result = judge.evaluate("tool", VALID_MANPAGE, "context text")
    assert result.score == 90
    assert result.passed is True
    assert result.summary == "High quality."


def test_llm_judge_score_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_synthesis(*args: Any, **kwargs: Any) -> str:
        return json.dumps(
            {
                "score": 60,
                "passed": True,
                "rubric_breakdown": {
                    "domain_ontology": 12,
                    "formatting": 12,
                    "subsystem_grouping": 12,
                    "environment_files_exit": 12,
                    "workflow_examples": 12,
                },
                "defects": ["Low score"],
                "summary": "Subpar quality.",
            }
        )

    monkeypatch.setattr("maniac.evaluation.judge.run_llm_synthesis", fake_synthesis)

    judge = LLMJudge(pass_threshold=70)
    result = judge.evaluate("tool", VALID_MANPAGE, "context text")
    assert result.score == 60
    assert result.passed is False


def test_evaluate_manpage_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pandoc")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            args=a[0], returncode=0, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr(
        "maniac.evaluation.judge.run_llm_synthesis",
        lambda *a, **kw: json.dumps(
            {
                "score": 92,
                "passed": True,
                "rubric_breakdown": {
                    "domain_ontology": 18,
                    "formatting": 19,
                    "subsystem_grouping": 18,
                    "environment_files_exit": 19,
                    "workflow_examples": 18,
                },
                "defects": [],
                "summary": "Great manual.",
            }
        ),
    )

    result = evaluate_manpage("tool", VALID_MANPAGE, "context")
    assert result.score == 92
    assert result.passed is True


def test_evaluate_manpage_deterministic_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(
        "maniac.evaluation.judge.run_llm_synthesis",
        lambda *a, **kw: json.dumps(
            {
                "score": 90,
                "passed": True,
                "rubric_breakdown": {
                    "domain_ontology": 18,
                    "formatting": 18,
                    "subsystem_grouping": 18,
                    "environment_files_exit": 18,
                    "workflow_examples": 18,
                },
                "defects": [],
                "summary": "Good.",
            }
        ),
    )

    result = evaluate_manpage("tool", VALID_MANPAGE, "context")
    assert not result.passed
    assert any("pandoc is not installed" in d for d in result.defects)


def test_cli_eval_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manpage_path = tmp_path / "tool.1.md"
    manpage_path.write_text(VALID_MANPAGE, encoding="utf-8")
    context_path = tmp_path / "tool_context.md"
    context_path.write_text("Context documentation", encoding="utf-8")

    monkeypatch.setattr(
        "maniac.cli.evaluate_manpage",
        lambda **kw: EvaluationResult(
            score=88,
            passed=True,
            rubric_breakdown={
                "domain_ontology": 18,
                "formatting": 18,
                "subsystem_grouping": 17,
                "environment_files_exit": 17,
                "workflow_examples": 18,
            },
            defects=[],
            summary="Solid manual.",
        ),
    )

    res = runner.invoke(
        app,
        [
            "eval",
            "tool",
            "--manpage-file",
            str(manpage_path),
            "--context-file",
            str(context_path),
        ],
    )
    assert res.exit_code == 0
    assert "PASSED" in res.output
    assert "Quality Evaluation: tool (88/100)" in res.output


def test_cli_eval_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manpage_path = tmp_path / "tool.1.md"
    manpage_path.write_text(VALID_MANPAGE, encoding="utf-8")
    context_path = tmp_path / "tool_context.md"
    context_path.write_text("Context documentation", encoding="utf-8")

    monkeypatch.setattr(
        "maniac.cli.evaluate_manpage",
        lambda **kw: EvaluationResult(
            score=55,
            passed=False,
            rubric_breakdown={
                "domain_ontology": 10,
                "formatting": 10,
                "subsystem_grouping": 12,
                "environment_files_exit": 11,
                "workflow_examples": 12,
            },
            defects=["Missing detailed examples."],
            summary="Needs improvement.",
        ),
    )

    res = runner.invoke(
        app,
        [
            "eval",
            "tool",
            "--manpage-file",
            str(manpage_path),
            "--context-file",
            str(context_path),
        ],
    )
    assert res.exit_code == 1
    assert "FAILED" in res.output
    assert "Missing detailed examples." in res.output


def test_cli_eval_missing_files() -> None:
    res = runner.invoke(app, ["eval", "nonexistent_binary_xyz_123"])
    assert res.exit_code == 1
    assert "Error: Manpage not found" in res.output
