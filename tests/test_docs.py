from pathlib import Path
from typing import Any

import pytest

from maniac.models import DocFile, RepoSource
from maniac.sources.docs import (
    extract_docs_from_dir,
    fetch_and_extract_docs,
    format_docs_section,
)


def test_extract_docs_from_dir(tmp_path: Path) -> None:
    # Create sample structure
    (tmp_path / "README.md").write_text("# My Tool\nSample readme", encoding="utf-8")
    (tmp_path / "USAGE.md").write_text("Usage guide", encoding="utf-8")
    (tmp_path / "CLI.md").write_text("CLI options reference", encoding="utf-8")
    (tmp_path / "random.bin").write_text("binary data", encoding="utf-8")

    docs_dir = tmp_path / "documentation"
    docs_dir.mkdir()
    (docs_dir / "guide.md").write_text("Detailed guide", encoding="utf-8")
    (docs_dir / "index.rst").write_text("Sphinx index", encoding="utf-8")

    # Ignored directory
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_doc.md").write_text("Should be ignored", encoding="utf-8")

    doc_files = extract_docs_from_dir(tmp_path)
    rel_paths = {d.rel_path for d in doc_files}

    assert "README.md" in rel_paths
    assert "USAGE.md" in rel_paths
    assert "CLI.md" in rel_paths
    assert "documentation/guide.md" in rel_paths
    assert "documentation/index.rst" in rel_paths
    assert "tests/test_doc.md" not in rel_paths


def test_fetch_and_extract_docs_local(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Local Project", encoding="utf-8")
    source = RepoSource(
        name="localtool",
        target=f"LOCAL:{tmp_path}",
        is_local=True,
        local_path=tmp_path,
    )

    doc_files = fetch_and_extract_docs(source)
    assert len(doc_files) == 1
    assert doc_files[0].rel_path == "README.md"
    assert doc_files[0].content == "# Local Project"


def test_format_docs_section() -> None:
    files = [
        DocFile(rel_path="README.md", content="# Tool"),
        DocFile(rel_path="docs/guide.md", content="## Guide"),
    ]
    formatted = format_docs_section(files)
    assert "### README.md" in formatted
    assert "### docs/guide.md" in formatted
    assert "# Tool" in formatted


def test_fetch_and_extract_docs_git_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.SubprocessError("Git clone failed")

    monkeypatch.setattr(subprocess, "run", fake_run)
    source = RepoSource(
        name="remote_tool",
        target="test/remote_tool",
        is_local=False,
    )
    docs = fetch_and_extract_docs(source, cache_dir=tmp_path)
    assert docs == []


def test_fetch_and_extract_docs_no_clone_url() -> None:
    source = RepoSource(
        name="notarget",
        target="LOCAL:/nonexistent",
        is_local=True,
        local_path=Path("/nonexistent"),
    )
    docs = fetch_and_extract_docs(source)
    assert docs == []
