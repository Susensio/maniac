from pathlib import Path

import pytest

from maniac.models import DocFile, RepoSource
from maniac.orchestration.pipeline import run_pipeline


def test_run_pipeline_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands",
        lambda cmd: {"> testtool --help": "Usage: testtool"},
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.discover_repo",
        lambda name: RepoSource(name=name, target="org/testtool", is_local=False),
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: [
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

    # M1: intermediate_dir was never passed, so this only stays out of the
    # real ~/.local/state/maniac/ if the default resolves under tmp_path.
    assert result.context_path.is_relative_to(tmp_path)
    assert result.prompt_path.is_relative_to(tmp_path)
