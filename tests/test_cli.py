import io
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

import maniac.cli as cli_module
from maniac.cli import _render_eval_table, app
from maniac.installer import UninstallResult
from maniac.models import DocFile, EvaluationResult, RepoSource

runner = CliRunner()


@pytest.fixture(autouse=True)
def _plain_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the CLI's lazy console to a colourless one for this module.

    H1: `Console()` picks up ambient `FORCE_COLOR`, injecting ANSI codes that
    split plain-substring assertions. Tests must not depend on the shell's
    environment, so pin it here rather than changing production colour
    behaviour.
    """
    monkeypatch.setattr(
        cli_module.console,
        "_instance",
        Console(force_terminal=False, no_color=True),
    )


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Maniac:" in result.output
    assert "crawl" in result.output
    assert "generate" in result.output
    assert "eval" in result.output


def test_cli_crawl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "maniac.sources.crawler.find_subcommands",
        lambda cmd: {"> git --help": "git help content"},
    )
    result = runner.invoke(app, ["crawl", "git"])
    assert result.exit_code == 0
    assert "> git --help" in result.output
    assert "git help content" in result.output


def test_cli_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "maniac.sources.discovery.discover_repo",
        lambda tool: RepoSource(name=tool, target="test/tool", is_local=False),
    )
    monkeypatch.setattr(
        "maniac.sources.docs.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: [
            DocFile(rel_path="README.md", content="Content")
        ],
    )
    result = runner.invoke(app, ["docs", "mytool"])
    assert result.exit_code == 0
    assert "Discovered repository source" in result.output
    assert "README.md" in result.output


def test_cli_generate_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands",
        lambda cmd: {"> tool --help": "Usage: tool"},
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.discover_repo",
        lambda name: RepoSource(name=name, target="org/tool", is_local=False),
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: [
            DocFile(rel_path="README.md", content="# Tool")
        ],
    )

    result = runner.invoke(
        app,
        ["generate", "mytool", "--output-dir", str(tmp_path), "--dry-run"],
    )
    assert result.exit_code == 0
    assert "Successfully generated manpage" in result.output


def test_cli_verbose_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    result = runner.invoke(app, ["-v", "--help"])
    assert result.exit_code == 0
    assert "Maniac:" in result.output

    monkeypatch.setenv("MANIAC_VERBOSE", "1")
    result_env = runner.invoke(app, ["--help"])
    assert result_env.exit_code == 0


def test_render_eval_table() -> None:
    output_buf = io.StringIO()
    test_console = Console(file=output_buf, color_system=None, force_terminal=False)
    result = EvaluationResult(
        score=85,
        passed=True,
        rubric_breakdown={
            "domain_ontology": 17,
            "correctness_coverage": 18,
            "formatting": 18,
            "subsystem_grouping": 16,
            "environment_reference_examples": 16,
        },
        defects=["Minor defect note"],
        summary="Evaluation summary.",
    )
    _render_eval_table(test_console, "sampletool", result)
    output = output_buf.getvalue()
    assert "Quality Evaluation: sampletool (85/100)" in output
    assert "Domain Ontology & Architecture" in output
    assert "PASSED" in output
    assert "Evaluation summary." in output
    assert "Minor defect note" in output


def test_cli_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "maniac.installer.list_installed_manpages",
        lambda cfg: [
            {
                "tool": "mytool",
                "path": tmp_path / "mytool.1",
                "date": "2026-08-25",
                "model": "Flash",
                "has_backup": False,
            }
        ],
    )
    res = runner.invoke(app, ["list"])
    assert res.exit_code == 0
    assert "MANIAC-Managed Manpages" in res.output
    assert "mytool" in res.output


def test_cli_list_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("maniac.installer.list_installed_manpages", lambda cfg: [])
    res = runner.invoke(app, ["list"])
    assert res.exit_code == 0
    assert "No MANIAC-managed manpages found" in res.output


def test_cli_uninstall(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "maniac.installer.uninstall_manpage",
        lambda tool, purge, config: UninstallResult(removed=[tmp_path / f"{tool}.1"]),
    )
    res = runner.invoke(app, ["uninstall", "mytool"])
    assert res.exit_code == 0
    assert "Uninstalled manpage for mytool!" in res.output


def test_cli_uninstall_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "maniac.installer.uninstall_manpage",
        lambda tool, purge, config: UninstallResult(),
    )
    res = runner.invoke(app, ["uninstall", "nonexistent"])
    assert res.exit_code == 0
    assert "No installed manpage found for 'nonexistent'" in res.output


def test_cli_uninstall_foreign_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """M2: a foreign page left in `man_dir` must not be reported as fully uninstalled."""
    foreign_path = tmp_path / "man1" / "mytool.1"
    monkeypatch.setattr(
        "maniac.installer.uninstall_manpage",
        lambda tool, purge, config: UninstallResult(
            removed=[tmp_path / f"{tool}.1"], foreign_kept=foreign_path
        ),
    )
    res = runner.invoke(app, ["uninstall", "mytool"])
    assert res.exit_code == 0
    assert "Uninstalled manpage for mytool!" in res.output
    assert "Left non-MANIAC manpage in place" in res.output
    assert str(foreign_path) in res.output


def test_cli_list_missing_no_man(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    res = runner.invoke(app, ["list-missing", "--bin-dir", str(tmp_path)])
    assert res.exit_code == 1
    assert "'man' utility is not installed" in res.output


def test_cli_generate_multiple_all_fail_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    """M10: `generate` must not silently exit 0 when every tool fails."""
    from maniac.exceptions import ManiacError

    def _raise(*args: object, **kwargs: object) -> None:
        raise ManiacError("boom")

    monkeypatch.setattr("maniac.orchestration.pipeline.run_pipeline", _raise)
    res = runner.invoke(app, ["generate", "toolone", "tooltwo"])
    assert res.exit_code == 1
    assert "Generation failed for toolone: boom" in res.output
    assert "Generation failed for tooltwo: boom" in res.output
    assert "2/2 tool(s) failed" in res.output


def test_cli_generate_multiple_partial_success_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """M10: one failure among several tools still fails the multi-generation."""
    from maniac.exceptions import ManiacError
    from maniac.models import PipelineResult

    def _run_pipeline(tool_name: str, **kwargs: object) -> PipelineResult:
        if tool_name == "badtool":
            raise ManiacError("boom")
        return PipelineResult(
            tool_name=tool_name,
            repo_source=RepoSource(name=tool_name, target="org/repo", is_local=False),
            command_count=1,
            doc_file_count=1,
            context_path=None,
            prompt_path=None,
            markdown_path=tmp_path / f"{tool_name}.1.md",
            roff_path=None,
            installed_path=None,
            markdown_content="# doc",
        )

    monkeypatch.setattr("maniac.orchestration.pipeline.run_pipeline", _run_pipeline)
    res = runner.invoke(app, ["generate", "goodtool", "badtool"])
    assert res.exit_code == 1
    assert "1/2 tool(s) failed" in res.output


def test_cli_generate_missing_all_have_man(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import shutil
    import subprocess
    
    # Create fake bins
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "foo").touch()
    
    # Fake man saying it exists
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/man")
    monkeypatch.setattr(
        subprocess, "run", 
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="man exists", stderr="")
    )

    res = runner.invoke(app, ["generate-missing", "--bin-dir", str(bin_dir)])
    assert res.exit_code == 0
    assert "All binaries have manpages" in res.output
