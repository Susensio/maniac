import io
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

import maniac.cli as cli_module
from maniac.cli import _render_eval_table, _repo_cell, app
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

    No width pin: commands now compute a result structure that tests assert
    against directly (see `compute_eval`, `compute_compare`, `compute_uninstall`),
    so no remaining assertion depends on how Rich wraps a long dynamic value
    such as a path. A handful of rendering smoke tests below only check
    short, fixed strings that cannot wrap at any terminal width.
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
    assert "source" in result.output
    assert "install" in result.output
    assert "eval" in result.output
    assert "status" in result.output


def test_repo_cell_links_known_repo_and_labels_unknown() -> None:
    known = _repo_cell(
        RepoSource(name="rg", target="BurntSushi/ripgrep", is_local=False)
    )
    unknown = _repo_cell(RepoSource(name="rg", target="rg", is_local=False))

    assert known.plain == "BurntSushi/ripgrep"
    assert known.style.link == "https://github.com/BurntSushi/ripgrep"
    assert known.style.underline is None
    assert unknown.plain == "Unknown"


def test_repo_cell_aqua_backend_prefix_links_to_the_real_repo() -> None:
    """M-repo-cell-aqua: a Mise `tool_alias` naming aqua leaks its prefix into `target`.

    The link must point at the real GitHub repo, not
    `https://github.com/aqua:sharkdp/pastel` -- the backend prefix
    concatenated straight into the URL.
    """
    cell = _repo_cell(
        RepoSource(name="pastel", target="aqua:sharkdp/pastel", is_local=False)
    )

    assert cell.plain == "aqua:sharkdp/pastel"
    assert cell.style.link == "https://github.com/sharkdp/pastel"


def test_repo_cell_non_github_backend_prefix_renders_as_plain_text() -> None:
    """npm/pipx/cargo package names have no fixed relationship to a GitHub path."""
    cell = _repo_cell(RepoSource(name="eslint", target="npm:eslint", is_local=False))

    assert cell.plain == "npm:eslint"
    assert cell.style == "yellow"


def test_cli_source_crawl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "maniac.sources.crawler.find_subcommands",
        lambda cmd: {"> git --help": "git help content"},
    )
    result = runner.invoke(app, ["source", "crawl", "git"])
    assert result.exit_code == 0
    assert "> git --help" in result.output
    assert "git help content" in result.output


def test_cli_source_docs(monkeypatch: pytest.MonkeyPatch) -> None:
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
    result = runner.invoke(app, ["source", "docs", "mytool"])
    assert result.exit_code == 0
    assert "Discovered repository source" in result.output
    assert "README.md" in result.output


def test_cli_install_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
        ["install", "mytool", "--output-dir", str(tmp_path), "--dry-run"],
    )
    assert result.exit_code == 0
    assert "synthesized from --help" in result.output


def test_cli_install_always_installs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`install` has no `--no-install`: the command's own name is the answer."""
    from maniac.models import PipelineResult

    observed: dict[str, object] = {}

    def _run_pipeline(tool_name: str, **kwargs: object) -> PipelineResult:
        observed["install"] = kwargs["install"]
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

    res = runner.invoke(app, ["install", "mytool"])
    assert res.exit_code == 0
    assert observed == {"install": True}


def test_cli_install_rejects_the_removed_no_install_flag() -> None:
    """M-inverted-install: `--no-install` contradicted the command's own verb; dropped outright."""
    res = runner.invoke(app, ["install", "mytool", "--no-install"])
    assert res.exit_code == 2


def test_cli_install_zero_tools_exits_quietly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`$(maniac status)` can legitimately expand to nothing."""
    called = False

    def _run_pipeline(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("maniac.orchestration.pipeline.run_pipeline", _run_pipeline)

    res = runner.invoke(app, ["install"])
    assert res.exit_code == 0
    assert res.output == ""
    assert not called


def test_fish_completion_includes_only_dry_run() -> None:
    result = runner.invoke(
        app,
        [],
        prog_name="maniac",
        env={
            "_MANIAC_COMPLETE": "complete_fish",
            "_TYPER_COMPLETE_FISH_ACTION": "get-args",
            "_TYPER_COMPLETE_ARGS": "maniac install --dry",
        },
    )

    assert result.exit_code == 0
    assert result.output == "--dry-run\tSkip LLM synthesis.\n"

    result = runner.invoke(
        app,
        [],
        prog_name="maniac",
        env={
            "_MANIAC_COMPLETE": "complete_fish",
            "_TYPER_COMPLETE_FISH_ACTION": "get-args",
            "_TYPER_COMPLETE_ARGS": "maniac install --no-dry",
        },
    )

    assert result.exit_code == 0
    assert not result.output.strip()


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


def test_cli_uninstall(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from maniac.cli.uninstall import compute_uninstall

    monkeypatch.setattr(
        "maniac.installer.uninstall_manpage",
        lambda tool, purge, config: UninstallResult(removed=[tmp_path / f"{tool}.1"]),
    )
    outcome = compute_uninstall("mytool")
    assert outcome.result == UninstallResult(removed=[tmp_path / "mytool.1"])

    res = runner.invoke(app, ["uninstall", "mytool"])
    assert res.exit_code == 0
    assert "Uninstalled manpage for mytool!" in res.output


def test_cli_uninstall_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    from maniac.cli.uninstall import compute_uninstall

    monkeypatch.setattr(
        "maniac.installer.uninstall_manpage",
        lambda tool, purge, config: UninstallResult(),
    )
    outcome = compute_uninstall("nonexistent")
    assert outcome.result == UninstallResult()

    res = runner.invoke(app, ["uninstall", "nonexistent"])
    assert res.exit_code == 0
    assert "No installed manpage found for 'nonexistent'" in res.output


def test_cli_uninstall_foreign_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """M2: a foreign page left in `man_dir` must not be reported as fully uninstalled.

    Asserts on the returned structure rather than rendered text -- the
    foreign path is an arbitrary-length pytest tmp_path, and Rich would wrap
    it unpredictably depending on the ambient terminal width.
    """
    from maniac.cli.uninstall import compute_uninstall

    foreign_path = tmp_path / "man1" / "mytool.1"
    monkeypatch.setattr(
        "maniac.installer.uninstall_manpage",
        lambda tool, purge, config: UninstallResult(
            removed=[tmp_path / f"{tool}.1"], foreign_kept=foreign_path
        ),
    )
    outcome = compute_uninstall("mytool")
    assert outcome.result.removed == [tmp_path / "mytool.1"]
    assert outcome.result.foreign_kept == foreign_path

    res = runner.invoke(app, ["uninstall", "mytool"])
    assert res.exit_code == 0
    assert "Uninstalled manpage for mytool!" in res.output
    assert "Left non-MANIAC manpage in place" in res.output


def test_cli_install_multiple_all_fail_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M10: `install` must not silently exit 0 when every tool fails."""
    from maniac.exceptions import ManiacError

    def _raise(*args: object, **kwargs: object) -> None:
        raise ManiacError("boom")

    monkeypatch.setattr("maniac.orchestration.pipeline.run_pipeline", _raise)
    res = runner.invoke(app, ["install", "toolone", "tooltwo"])
    assert res.exit_code == 1
    assert "Install failed for toolone: boom" in res.output
    assert "Install failed for tooltwo: boom" in res.output
    assert "2/2 tool(s) failed" in res.output


def test_cli_install_multiple_partial_success_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """M10: one failure among several tools still fails the multi-install."""
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
    res = runner.invoke(app, ["install", "goodtool", "badtool"])
    assert res.exit_code == 1
    assert "1/2 tool(s) failed" in res.output


def test_render_install_does_not_swallow_bracketed_detail() -> None:
    """M-bracket-escape: `[no synthesis]` is literal text, not Rich markup.

    Rich reads an unescaped `[...]` as a style tag and silently drops it,
    so an un-escaped render would print "pandoc   upstream manpage from
    install root (3.10.2)" with the `[no synthesis]` suffix missing.
    """
    import io

    from rich.console import Console

    from maniac.cli.install import _render_install
    from maniac.orchestration.install import InstallOutcome, Tier

    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=False, no_color=True)
    outcome = InstallOutcome(
        tool="pandoc",
        tier=Tier.INSTALL_ROOT,
        detail="upstream manpage from install root (3.10.2)   [no synthesis]",
    )

    _render_install(test_console, outcome)

    assert "[no synthesis]" in buf.getvalue()
