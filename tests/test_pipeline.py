from pathlib import Path

import pytest

from maniac.exceptions import GenerationError
from maniac.models import DocFile, Installation, RepoSource
from maniac.orchestration.pipeline import run_pipeline


def _installation(version: str | None) -> Installation:
    return Installation(
        binary="testtool",
        bin_path=Path("/bin/testtool"),
        real_path=Path("/bin/testtool"),
        provider="fake",
        package="testtool",
        version=version,
        root=Path("/root"),
    )


def _mock_synthesis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> dict[str, object]:
    """Mocks the LLM/compile/install steps `install=True` reaches past `dry_run`.

    Returns the dict `install_manpage`'s kwargs land in, so a test can assert
    on the `version` it was actually called with.
    """
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.run_llm_synthesis",
        lambda prompt, **kwargs: "% TESTTOOL(1)\n\n# NAME\ntesttool",
    )

    def _compile_to_man(
        markdown_text: str, output_file: str | Path, **kwargs: object
    ) -> bool:
        Path(output_file).write_text(".TH TESTTOOL 1\n", encoding="utf-8")
        return True

    monkeypatch.setattr("maniac.orchestration.pipeline.compile_to_man", _compile_to_man)

    recorded: dict[str, object] = {}

    def _install_manpage(*args: object, **kwargs: object) -> Path:
        recorded["version"] = kwargs.get("version")
        return tmp_path / "testtool.1"

    monkeypatch.setattr(
        "maniac.orchestration.pipeline.install_manpage", _install_manpage
    )
    return recorded


def test_run_pipeline_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands",
        lambda cmd: {"> testtool --help": "Usage: testtool"},
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.discover_repo",
        lambda name, **kwargs: RepoSource(
            name=name, target="org/testtool", is_local=False
        ),
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: (
            [DocFile(rel_path="README.md", content="# Test Tool")],
            False,
        ),
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
    assert result.context_path is not None
    assert result.prompt_path is not None
    assert result.context_path.is_relative_to(tmp_path)
    assert result.prompt_path.is_relative_to(tmp_path)


def test_run_pipeline_uses_custom_bin_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    observed: dict[str, object] = {}

    def _find_subcommands(cmd: list[str]) -> dict[str, str]:
        observed["command"] = cmd
        return {"> testtool --help": "Usage: testtool"}

    def _discover_repo(name: str, **kwargs: object) -> RepoSource:
        observed["bin_dir"] = kwargs["bin_dir"]
        return RepoSource(name=name, target="org/testtool", is_local=False)

    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands", _find_subcommands
    )
    monkeypatch.setattr("maniac.orchestration.pipeline.discover_repo", _discover_repo)
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: (
            [DocFile(rel_path="README.md", content="# Test Tool")],
            False,
        ),
    )

    run_pipeline(
        tool_name="testtool", bin_dir=bin_dir, output_dir=tmp_path, dry_run=True
    )

    assert observed == {"command": [str(bin_dir / "testtool")], "bin_dir": bin_dir}


def test_run_pipeline_rejects_root_help_without_other_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands",
        lambda cmd: {"> testtool --help": "Usage: testtool"},
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.discover_repo",
        lambda name: RepoSource(name=name, target=name, is_local=False),
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: ([], False),
    )

    with pytest.raises(GenerationError, match="Not enough source material"):
        run_pipeline(tool_name="testtool", output_dir=tmp_path, dry_run=True)


def test_run_pipeline_records_the_matched_tag_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0019: docs that came from a version-matched tag earn a recorded version."""
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands",
        lambda cmd: {"> testtool --help": "Usage: testtool"},
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.discover_repo",
        lambda name, **kwargs: RepoSource(
            name=name, target="org/testtool", is_local=False
        ),
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_installation",
        lambda name, bin_dir=None: (None, _installation("1.2.3")),
    )

    observed_version: dict[str, object] = {}

    def _fetch_and_extract_docs(
        source: RepoSource, cache_dir: Path, **kwargs: object
    ) -> tuple[list[DocFile], bool]:
        observed_version["passed"] = kwargs.get("version")
        return [DocFile(rel_path="README.md", content="# Test Tool")], True

    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs", _fetch_and_extract_docs
    )
    recorded = _mock_synthesis(monkeypatch, tmp_path)

    run_pipeline(tool_name="testtool", output_dir=tmp_path, install=True, dry_run=False)

    assert observed_version["passed"] == "1.2.3"
    assert recorded["version"] == "1.2.3"


def test_run_pipeline_records_no_version_when_tag_unmatched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0019: no matching tag means no recorded version, even though the
    installed binary has one -- `install_manpage` must not be handed
    `inst.version` on the strength of it being non-None; a page built from
    the default branch has not earned it.
    """
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands",
        lambda cmd: {"> testtool --help": "Usage: testtool"},
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.discover_repo",
        lambda name, **kwargs: RepoSource(
            name=name, target="org/testtool", is_local=False
        ),
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_installation",
        lambda name, bin_dir=None: (None, _installation("9.9.9")),
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: (
            [DocFile(rel_path="README.md", content="# Default branch")],
            False,
        ),
    )
    recorded = _mock_synthesis(monkeypatch, tmp_path)

    run_pipeline(tool_name="testtool", output_dir=tmp_path, install=True, dry_run=False)

    assert recorded["version"] is None


def test_run_pipeline_no_installation_records_no_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No `Installation` in scope (`find_installation` finds nothing) means no
    version to match against at all, so extraction gets `version=None` and
    synthesis still completes and installs -- unversioned, not blocked.
    """
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands",
        lambda cmd: {"> testtool --help": "Usage: testtool"},
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.discover_repo",
        lambda name, **kwargs: RepoSource(
            name=name, target="org/testtool", is_local=False
        ),
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_installation",
        lambda name, bin_dir=None: None,
    )

    observed_version: dict[str, object] = {}

    def _fetch_and_extract_docs(
        source: RepoSource, cache_dir: Path, **kwargs: object
    ) -> tuple[list[DocFile], bool]:
        observed_version["passed"] = kwargs.get("version")
        return [DocFile(rel_path="README.md", content="# Test Tool")], False

    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs", _fetch_and_extract_docs
    )
    recorded = _mock_synthesis(monkeypatch, tmp_path)

    run_pipeline(tool_name="testtool", output_dir=tmp_path, install=True, dry_run=False)

    assert observed_version["passed"] is None
    assert recorded["version"] is None
