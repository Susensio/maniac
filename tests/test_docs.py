from pathlib import Path

from maniac.docs import (
    extract_docs_from_dir,
    fetch_and_extract_docs,
    format_docs_section,
)
from maniac.models import DocFile, RepoSource


def test_extract_docs_from_dir(tmp_path: Path) -> None:
    # Create sample structure
    (tmp_path / "README.md").write_text("# My Tool\nSample readme", encoding="utf-8")
    (tmp_path / "USAGE.md").write_text("Usage guide", encoding="utf-8")
    (tmp_path / "random.bin").write_text("binary data", encoding="utf-8")

    docs_dir = tmp_path / "docs"
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
    assert "docs/guide.md" in rel_paths
    assert "docs/index.rst" in rel_paths
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
