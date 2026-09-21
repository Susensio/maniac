"""Tier-3 synthesis: both entry paths, source material, ADR-0019's recorded version."""

from pathlib import Path
from typing import cast

import pytest

from maniac.config import Config
from maniac.exceptions import CrawlerError, GenerationError
from maniac.installer import InstallResult
from maniac.manifest import Entry
from maniac.models import DocFile, Installation, RepoSource
from maniac.orchestration.context import ResolvedTool, resolve_tool
from maniac.orchestration.pipeline import synthesize


class _FakeProvider:
    """Minimal `Provider` stand-in answering with one fixed source."""

    name = "fake"

    def __init__(self, source: RepoSource | None = None) -> None:
        self._source = source

    def detect(self, bin_path: Path) -> Installation | None:
        return None

    def resolve_source(
        self, inst: Installation, *, config: Config, sources: object
    ) -> RepoSource | None:
        return self._source

    def local_docs(self, inst: Installation) -> list[Path]:
        return []


def _installation(version: str | None, binary: str = "testtool") -> Installation:
    return Installation(
        binary=binary,
        bin_path=Path("/bin") / binary,
        real_path=Path("/bin") / binary,
        provider="fake",
        package=binary,
        version=version,
        root=Path("/root"),
    )


def _resolved(
    tool_name: str = "testtool",
    *,
    target: str | None = "org/testtool",
    version: str | None = None,
    claimed: bool = True,
    bin_dir: Path | None = None,
    output_dir: Path | None = None,
) -> ResolvedTool:
    """A `ResolvedTool` as `run_install` would hand one to tier 3.

    `claimed=False` is the ADR-0020 case: no provider claims the binary, so
    there is no installation and no documentation source behind it.
    """
    source = (
        RepoSource(name=tool_name, target=target, is_local=False)
        if target is not None
        else None
    )
    config = Config(output_dir=output_dir) if output_dir is not None else Config()
    return ResolvedTool(
        tool_name=tool_name,
        config=config,
        cache_dir=config.cache_dir,
        bin_dir=bin_dir,
        provider=_FakeProvider(source) if claimed else None,
        installation=_installation(version, tool_name) if claimed else None,
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

    def _install_manpage(*args: object, **kwargs: object) -> InstallResult:
        entry = cast(Entry, args[2])
        recorded["version"] = entry.version
        return InstallResult(
            path=tmp_path / "testtool.1", materialized=None, backup_path=None
        )

    monkeypatch.setattr(
        "maniac.orchestration.pipeline.install_manpage", _install_manpage
    )
    return recorded


def _help_tree(monkeypatch: pytest.MonkeyPatch, tree: dict[str, str]) -> None:
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands", lambda cmd, **kwargs: tree
    )


def _one_doc_file(matched: bool) -> tuple[list[DocFile], bool]:
    return [DocFile(rel_path="README.md", content="# Test Tool")], matched


def test_synthesize_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A dry run is a true preview: it reports what synthesis would produce
    and where, but writes nothing -- not `output_dir`, which this test
    would otherwise have to create up front for `markdown_path` to land in.
    """
    _help_tree(monkeypatch, {"> testtool --help": "Usage: testtool"})
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: _one_doc_file(False),
    )
    out_dir = tmp_path / "manpages"

    result = synthesize(_resolved(output_dir=out_dir), dry_run=True)

    assert result.tool_name == "testtool"
    assert result.command_count == 1
    assert result.doc_file_count == 1
    assert not out_dir.exists()
    assert not result.markdown_path.exists()
    assert "% TESTTOOL(1)" in result.markdown_content

    # M1: intermediate_dir was never passed, so this only stays out of the
    # real ~/.local/state/maniac/ if the default resolves under tmp_path.
    assert result.context_path is not None
    assert result.context_path.is_relative_to(tmp_path)
    assert not result.context_path.exists()


def test_synthesize_consumes_the_facts_it_was_given_without_resolving_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The `install` entry path: tier 3 must never re-resolve supplied facts."""

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("tier 3 re-resolved an installation it was handed")

    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation", _fail
    )
    _help_tree(monkeypatch, {"> testtool --help": "Usage: testtool"})
    observed: list[RepoSource] = []
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: (
            observed.append(source) or _one_doc_file(True)
        ),
    )

    result = synthesize(_resolved(version="1.2.3", output_dir=tmp_path), dry_run=True)

    assert observed == [
        RepoSource(name="testtool", target="org/testtool", is_local=False)
    ]
    assert result.repo_source == observed[0]


def test_resolve_tool_is_the_direct_synthesis_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The direct path: a bare name, resolved explicitly, then synthesized."""
    provider = _FakeProvider(
        RepoSource(name="testtool", target="org/testtool", is_local=False)
    )
    inst = _installation("1.2.3")
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    _help_tree(monkeypatch, {"> testtool --help": "Usage: testtool"})
    observed: dict[str, object] = {}

    def _fetch_and_extract_docs(
        source: RepoSource, cache_dir: Path, **kwargs: object
    ) -> tuple[list[DocFile], bool]:
        observed["version"] = kwargs.get("version")
        return _one_doc_file(True)

    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs", _fetch_and_extract_docs
    )

    tool = resolve_tool("testtool", config=Config(output_dir=tmp_path))
    result = synthesize(tool, dry_run=True)

    assert tool.installation is inst
    assert observed["version"] == "1.2.3"
    assert result.repo_source is not None
    assert result.repo_source.target == "org/testtool"


def test_synthesize_fetches_docs_from_the_canonical_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _help_tree(monkeypatch, {"> tmux --help": "Usage: tmux"})
    observed: list[RepoSource] = []
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: (
            observed.append(source)
            or ([DocFile(rel_path="README.md", content="# Tmux")], False)
        ),
    )

    tool = _resolved("tmux", target="tmux/tmux-builds", output_dir=tmp_path)
    result = synthesize(tool, dry_run=True)

    assert observed == [RepoSource(name="tmux", target="tmux/tmux", is_local=False)]
    assert result.repo_source == observed[0]


def test_synthesize_uses_a_custom_bin_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    observed: dict[str, object] = {}

    def _find_subcommands(cmd: list[str], **kwargs: object) -> dict[str, str]:
        observed["command"] = cmd
        return {"> testtool --help": "Usage: testtool"}

    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands", _find_subcommands
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: _one_doc_file(False),
    )

    synthesize(_resolved(bin_dir=bin_dir, output_dir=tmp_path), dry_run=True)

    assert observed == {"command": [str(bin_dir / "testtool")]}


def test_synthesize_from_root_help_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _help_tree(monkeypatch, {"> testtool --help": "Usage: testtool"})
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: ([], False),
    )

    result = synthesize(_resolved(output_dir=tmp_path), dry_run=True)

    assert result.command_count == 1
    assert result.doc_file_count == 0


def test_synthesize_reports_its_source_material(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    messages: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.logger.warning",
        lambda event, **kwargs: messages.append((event, kwargs)),
    )
    _help_tree(
        monkeypatch,
        {
            "> testtool --help": "Usage: testtool",
            "> testtool run --help": "Usage: testtool run",
        },
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: _one_doc_file(True),
    )

    synthesize(_resolved(output_dir=tmp_path), dry_run=True)

    assert (
        "Synthesis source material found",
        {
            "tool": "testtool",
            "commands": 2,
            "subcommands": 1,
            "repository": "org/testtool",
            "repository_docs": 1,
            "repository_docs_version_matched": True,
        },
    ) in messages


def test_synthesize_warns_when_only_root_help_is_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    messages: list[str] = []
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.logger.warning",
        lambda event, **kwargs: messages.append(event),
    )
    _help_tree(monkeypatch, {"> testtool --help": "Usage: testtool"})
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: ([], False),
    )

    synthesize(_resolved(output_dir=tmp_path), dry_run=True)

    assert "Limited source material: synthesizing from root --help only" in messages


def test_synthesize_from_repository_docs_without_help(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands",
        lambda cmd, **kwargs: (_ for _ in ()).throw(CrawlerError("no help")),
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: _one_doc_file(True),
    )

    result = synthesize(_resolved(output_dir=tmp_path), dry_run=True)

    assert result.command_count == 0
    assert result.doc_file_count == 1


def test_synthesize_rejects_when_no_help_or_repository_docs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands",
        lambda cmd, **kwargs: (_ for _ in ()).throw(CrawlerError("no help")),
    )

    with pytest.raises(GenerationError, match="no usable --help output"):
        synthesize(_resolved(target=None, output_dir=tmp_path), dry_run=True)


def test_synthesize_records_the_matched_tag_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0019: docs that came from a version-matched tag earn a recorded version."""
    _help_tree(monkeypatch, {"> testtool --help": "Usage: testtool"})
    observed: dict[str, object] = {}

    def _fetch_and_extract_docs(
        source: RepoSource, cache_dir: Path, **kwargs: object
    ) -> tuple[list[DocFile], bool]:
        observed["version"] = kwargs.get("version")
        return _one_doc_file(True)

    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs", _fetch_and_extract_docs
    )
    recorded = _mock_synthesis(monkeypatch, tmp_path)

    synthesize(_resolved(version="1.2.3", output_dir=tmp_path), install=True)

    assert observed["version"] == "1.2.3"
    assert recorded["version"] == "1.2.3"


def test_synthesize_records_no_version_when_tag_unmatched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0019: no matching tag means no recorded version, even though the
    installed binary has one -- `install_manpage` must not be handed
    `inst.version` on the strength of it being non-None; a page built from
    the default branch has not earned it.
    """
    _help_tree(monkeypatch, {"> testtool --help": "Usage: testtool"})
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: (
            [DocFile(rel_path="README.md", content="# Default branch")],
            False,
        ),
    )
    recorded = _mock_synthesis(monkeypatch, tmp_path)

    synthesize(_resolved(version="9.9.9", output_dir=tmp_path), install=True)

    assert recorded["version"] is None


def test_synthesize_without_an_installation_records_no_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tool nothing claims has no version to match against at all, so
    extraction gets `version=None` and synthesis still completes and
    installs -- unversioned, not blocked.
    """
    _help_tree(monkeypatch, {"> testtool --help": "Usage: testtool"})
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.get_version", lambda cmd, **kwargs: None
    )
    recorded = _mock_synthesis(monkeypatch, tmp_path)

    synthesize(_resolved(claimed=False, output_dir=tmp_path), install=True)

    assert recorded["version"] is None


def test_synthesize_unclaimed_binary_records_its_own_version_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0020: no provider claims the binary, so there is no `Installation`
    to match a doc tag against -- but the binary's own `--version` output is
    matched evidence for the help-only page just crawled, and gets recorded
    verbatim.
    """
    _help_tree(monkeypatch, {"> testtool --help": "Usage: testtool"})
    observed: dict[str, object] = {}

    def _get_version(cmd: list[str], **kwargs: object) -> str:
        observed["cmd"] = cmd
        return "testtool 9.9.9-custom"

    monkeypatch.setattr("maniac.orchestration.pipeline.get_version", _get_version)
    recorded = _mock_synthesis(monkeypatch, tmp_path)

    synthesize(_resolved(claimed=False, output_dir=tmp_path), install=True)

    assert observed["cmd"] == ["testtool"]
    assert recorded["version"] == "testtool 9.9.9-custom"


def test_synthesize_dry_run_opens_no_manifest_transaction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`install=True` under `dry_run` must still never reach `install_manpage`
    -- the only call in this module that opens a manifest transaction.
    """
    _help_tree(monkeypatch, {"> testtool --help": "Usage: testtool"})
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: _one_doc_file(False),
    )

    def _explode(*args: object, **kwargs: object) -> Path:
        raise AssertionError("install_manpage reached under dry_run")

    monkeypatch.setattr("maniac.orchestration.pipeline.install_manpage", _explode)

    result = synthesize(
        _resolved(output_dir=tmp_path / "manpages"), install=True, dry_run=True
    )

    assert result.installed_path is None
