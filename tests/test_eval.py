import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from maniac.cli import app
from maniac.evaluation import (
    build_evaluation_prompt,
    check_metadata_header,
    check_pandoc_compilation,
    check_standard_sections,
    compute_coverage,
    evaluate_manpage,
    parse_evaluation_json,
    run_deterministic_checks,
    run_llm_judge,
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


def test_check_metadata_header_valid() -> None:
    ok, err = check_metadata_header(VALID_MANPAGE, tool_name="tool")
    assert ok
    assert err is None


def test_check_metadata_header_empty() -> None:
    ok, err = check_metadata_header("")
    assert not ok
    assert err == "Empty markdown content; missing metadata header."


def test_check_metadata_header_missing_prefix() -> None:
    ok, err = check_metadata_header("# NAME\ntool\n")
    assert not ok
    assert "First line must start with '% '" in (err or "")


def test_check_metadata_header_bad_format() -> None:
    ok, err = check_metadata_header("% TOOL invalid header")
    assert not ok
    assert "Metadata header does not match expected format" in (err or "")


def test_check_metadata_header_mismatched_tool() -> None:
    ok, err = check_metadata_header(VALID_MANPAGE, tool_name="other")
    assert not ok
    assert "does not match expected tool 'OTHER'" in (err or "")


def test_check_standard_sections_valid() -> None:
    ok, errors = check_standard_sections(VALID_MANPAGE)
    assert ok
    assert len(errors) == 0


def test_check_standard_sections_missing() -> None:
    bad_md = "% TOOL(1) | User Commands\n\n# NAME\ntool\n# SYNOPSIS\ntool\n# DESCRIPTION\ntool"
    ok, errors = check_standard_sections(bad_md)
    assert not ok
    assert any("Missing required section: #EXIT STATUS" in e for e in errors)
    assert any("Missing required section: #EXAMPLES" in e for e in errors)
    assert any(
        "Missing required section: at least one of #OPTIONS or #COMMANDS" in e
        for e in errors
    )


def test_check_pandoc_compilation_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pandoc")

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        cmd_args = args[0]
        assert "-f" in cmd_args
        assert cmd_args[cmd_args.index("-f") + 1] == "markdown-smart"
        return subprocess.CompletedProcess(
            args=cmd_args, returncode=0, stdout=".TH TOOL 1", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, err = check_pandoc_compilation(VALID_MANPAGE)
    assert ok
    assert err is None


def test_check_pandoc_compilation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pandoc")

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="Pandoc syntax error"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, err = check_pandoc_compilation(VALID_MANPAGE)
    assert not ok
    assert "Pandoc compilation failed: Pandoc syntax error" in (err or "")


def test_check_pandoc_compilation_no_pandoc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing pandoc binary is a host problem, not a document defect."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    ok, err = check_pandoc_compilation(VALID_MANPAGE)
    assert ok
    assert err is None


def test_compute_coverage_full() -> None:
    context = """> tool --help
Usage: tool [OPTIONS] <COMMAND>

Commands:
  run  Execute task

Options:
  -v, --verbose  Verbose mode
"""
    cov = compute_coverage(VALID_MANPAGE, context)
    assert cov.cmd_pct == 100.0
    assert "run" in cov.found_cmds
    assert len(cov.missing_cmds) == 0
    assert cov.flag_pct == 100.0
    assert "-v" in cov.found_flags
    assert "--verbose" in cov.found_flags


def test_compute_coverage_partial() -> None:
    context = """> tool --help
Commands:
  run     Execute task
  deploy  Deploy task

Options:
  -v, --verbose  Verbose mode
  -c, --config   Config file
"""
    cov = compute_coverage(VALID_MANPAGE, context)
    assert "run" in cov.found_cmds
    assert "deploy" in cov.missing_cmds
    assert cov.cmd_pct == 50.0
    assert "-v" in cov.found_flags
    assert "--verbose" in cov.found_flags
    assert "-c" in cov.missing_flags
    assert "--config" in cov.missing_flags


def test_compute_coverage_empty_context() -> None:
    cov = compute_coverage(VALID_MANPAGE, "")
    assert cov.cmd_pct == 100.0
    assert cov.flag_pct == 100.0
    assert len(cov.found_cmds) == 0
    assert len(cov.found_flags) == 0


def test_run_deterministic_checks_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pandoc")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            args=a[0], returncode=0, stdout=".TH TOOL 1", stderr=""
        ),
    )

    passed, defects = run_deterministic_checks(VALID_MANPAGE, tool_name="tool")
    assert passed
    assert len(defects) == 0


def test_run_deterministic_checks_with_coverage_defects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pandoc")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            args=a[0], returncode=0, stdout=".TH TOOL 1", stderr=""
        ),
    )

    context = """> tool missingcmd --help
Commands:
  missingcmd  Missing command
Options:
  --missing-flag  Missing flag
"""
    passed, defects = run_deterministic_checks(
        VALID_MANPAGE, tool_name="tool", context_text=context
    )
    assert not passed
    assert any("Missing subcommands from context" in d for d in defects)
    assert any("Missing flags from context" in d for d in defects)


def test_build_evaluation_prompt() -> None:
    prompt = build_evaluation_prompt(
        "mytool", VALID_MANPAGE, "> mytool --help\nUsage: mytool"
    )
    assert "=== REFERENCE CONTEXT FOR 'mytool' ===" in prompt
    assert (
        "=== RENDERED TERMINAL MANUAL PAGE (WHAT THE USER SEES IN `man mytool`) ==="
        in prompt
    )
    assert "=== RAW SOURCE MARKDOWN ===" in prompt
    assert "=== AUTOMATED COVERAGE ANALYSIS ===" in prompt


def test_compute_coverage_ignores_repo_docs() -> None:
    context = """# tool Extracted Context

## CLI Help
```text
> tool --help
Usage: tool [OPTIONS]

Options:
  -v, --verbose  Verbose mode
```

## Repository Documentation
Run cargo build --release --features extra -m "commit"
"""
    cov = compute_coverage(VALID_MANPAGE, context)
    assert cov.flag_pct == 100.0
    assert "--release" not in cov.found_flags
    assert "--release" not in cov.missing_flags
    assert "-v" in cov.found_flags


def test_compute_coverage_inverted_section_order() -> None:
    context = """# tool Extracted Context

## Repository Documentation
Run cargo build --release --features extra -m "commit"

## CLI Help
```text
> tool --help
Usage: tool [OPTIONS]

Options:
  -v, --verbose  Verbose mode
```
"""
    cov = compute_coverage(VALID_MANPAGE, context)
    assert cov.flag_pct == 100.0
    assert "--release" not in cov.found_flags
    assert "-v" in cov.found_flags


def test_check_standard_sections_synonyms_and_subheaders() -> None:
    md = """% TOOL(1) | User Commands

# NAME
tool - demonstration

## USAGE:
tool [OPTIONS]

# DESCRIPTION
Description.

## FLAGS:
-v, --verbose: verbose

# EXIT CODES
0: success

# EXAMPLES
tool -v
"""
    ok, errors = check_standard_sections(md)
    assert ok
    assert len(errors) == 0


def test_deterministic_checks_missing_flags_advisory_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pandoc")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            args=a[0], returncode=0, stdout=".TH TOOL 1", stderr=""
        ),
    )

    context = """> tool --help
Options:
  --extra-flag  Extra flag not in manpage
"""
    passed, defects = run_deterministic_checks(
        VALID_MANPAGE, tool_name="tool", context_text=context
    )
    assert passed
    assert any("Missing flags from context" in d for d in defects)


def test_parse_evaluation_json_invalid_escape_repaired() -> None:
    raw_with_invalid_escape = r'{"score": 90, "passed": true, "rubric_breakdown": {"domain_ontology": 18, "correctness_coverage": 18, "formatting": 18, "subsystem_grouping": 18, "environment_reference_examples": 18}, "defects": ["Invalid \escape sequence in LLM output"], "summary": "Great manual."}'
    result = parse_evaluation_json(raw_with_invalid_escape)
    assert result.score == 90
    assert result.passed is True
    assert "Invalid \\escape sequence in LLM output" in result.defects[0]


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


def test_parse_evaluation_json_new_schema() -> None:
    raw = json.dumps(
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
            "summary": "High quality manual.",
        }
    )
    result = parse_evaluation_json(raw)
    assert result.score == 92
    assert result.passed is True
    assert result.rubric_breakdown["correctness_coverage"] == 19
    assert result.rubric_breakdown["environment_reference_examples"] == 19


def test_parse_evaluation_json_string_fallback_rubric() -> None:
    raw = json.dumps(
        {
            "score": 90,
            "passed": True,
            "rubric_breakdown": {
                "domain_ontology": "18",
                "correctness": "17",
                "formatting": "18",
                "subsystem_grouping": "18",
                "environment_files_exit": "18",
                "workflow_examples": "17",
            },
            "defects": [],
            "summary": "String typed rubric breakdown.",
        }
    )
    result = parse_evaluation_json(raw)
    assert result.rubric_breakdown["domain_ontology"] == 18
    assert result.rubric_breakdown["correctness_coverage"] == 17
    assert result.rubric_breakdown["environment_reference_examples"] == 18
    assert result.score == 90


def test_parse_evaluation_json_malformed() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_evaluation_json("not valid json")


def test_run_llm_judge_mock(monkeypatch: pytest.MonkeyPatch) -> None:
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

    result = run_llm_judge("tool", VALID_MANPAGE, "context text", pass_threshold=70)
    assert result.score == 90
    assert result.passed is True
    assert result.summary == "High quality."


def test_run_llm_judge_score_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
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

    result = run_llm_judge("tool", VALID_MANPAGE, "context text", pass_threshold=70)
    assert result.score == 60
    assert result.passed is False


def test_run_llm_judge_score_clears_lower_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A --min-score below 70 must not be inert: a score clearing it should pass."""

    def fake_synthesis(*args: Any, **kwargs: Any) -> str:
        return json.dumps(
            {
                "score": 60,
                "passed": False,
                "rubric_breakdown": {
                    "domain_ontology": 12,
                    "formatting": 12,
                    "subsystem_grouping": 12,
                    "environment_files_exit": 12,
                    "workflow_examples": 12,
                },
                "defects": [],
                "summary": "Adequate quality.",
            }
        )

    monkeypatch.setattr("maniac.evaluation.judge.run_llm_synthesis", fake_synthesis)

    result = run_llm_judge("tool", VALID_MANPAGE, "context text", pass_threshold=50)
    assert result.score == 60
    assert result.passed is True


def test_run_llm_judge_malformed_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "maniac.evaluation.judge.run_llm_synthesis",
        lambda *a, **kw: "invalid json",
    )

    result = run_llm_judge("tool", VALID_MANPAGE, "context text")
    assert result.score == 0
    assert result.passed is False
    assert any("Failed to parse LLM judge response" in d for d in result.defects)


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
    """Deterministic failures (here: Pandoc compilation error) surface in result.passed."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pandoc")

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="Pandoc syntax error"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
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
    assert any("Pandoc compilation failed" in d for d in result.defects)


def test_cli_eval_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manpage_path = tmp_path / "tool.1.md"
    manpage_path.write_text(VALID_MANPAGE, encoding="utf-8")
    context_path = tmp_path / "tool_context.md"
    context_path.write_text("Context documentation", encoding="utf-8")

    monkeypatch.setattr(
        "maniac.evaluation.judge.evaluate_manpage",
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
        "maniac.evaluation.judge.evaluate_manpage",
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
