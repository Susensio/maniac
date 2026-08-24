from pathlib import Path

import pytest
from typer.testing import CliRunner

from maniac.cli import app
from maniac.models import DocFile, RepoSource

runner = CliRunner()


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Maniac:" in result.output
    assert "crawl" in result.output
    assert "generate" in result.output


def test_cli_crawl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "maniac.cli.find_subcommands",
        lambda cmd: {"> git --help": "git help content"},
    )
    result = runner.invoke(app, ["crawl", "git"])
    assert result.exit_code == 0
    assert "> git --help" in result.output
    assert "git help content" in result.output


def test_cli_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "maniac.cli.discover_repo",
        lambda tool: RepoSource(name=tool, target="test/tool", is_local=False),
    )
    monkeypatch.setattr(
        "maniac.cli.fetch_and_extract_docs",
        lambda source, cache_dir: [DocFile(rel_path="README.md", content="Content")],
    )
    result = runner.invoke(app, ["docs", "mytool"])
    assert result.exit_code == 0
    assert "Discovered repository source" in result.output
    assert "README.md" in result.output


def test_cli_generate_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "maniac.pipeline.find_subcommands",
        lambda cmd: {"> tool --help": "Usage: tool"},
    )
    monkeypatch.setattr(
        "maniac.pipeline.discover_repo",
        lambda name: RepoSource(name=name, target="org/tool", is_local=False),
    )
    monkeypatch.setattr(
        "maniac.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir: [DocFile(rel_path="README.md", content="# Tool")],
    )

    result = runner.invoke(
        app,
        ["generate", "mytool", "--output-dir", str(tmp_path), "--dry-run"],
    )
    assert result.exit_code == 0
    assert "Successfully generated manpage" in result.output
