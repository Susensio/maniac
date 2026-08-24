from pathlib import Path

import pytest

from maniac.models import DocFile, RepoSource
from maniac.pipeline import run_pipeline


def test_run_pipeline_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "maniac.pipeline.find_subcommands",
        lambda cmd: {"> testtool --help": "Usage: testtool"},
    )
    monkeypatch.setattr(
        "maniac.pipeline.discover_repo",
        lambda name: RepoSource(name=name, target="org/testtool", is_local=False),
    )
    monkeypatch.setattr(
        "maniac.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir: [
            DocFile(rel_path="README.md", content="# Test Tool")
        ],
    )

    out_dir = tmp_path / "manpages"
    result = run_pipeline(
        tool_name="testtool",
        output_dir=out_dir,
        dry_run=True,
    )

    assert result.tool_name == "testtool"
    assert result.command_count == 1
    assert result.doc_file_count == 1
    assert result.markdown_path.exists()
    assert "% TESTTOOL(1)" in result.markdown_content
