"""Tests for `run_install` (ADR-0016): tier selection, `--generate`/`--no-generate`."""

from pathlib import Path

import pytest

from maniac.models import Installation, RepoSource
from maniac.orchestration.install import Tier, run_install


class _FakeProvider:
    """Minimal `Provider` stand-in with per-test-configurable answers."""

    name = "fake"

    def __init__(
        self,
        local_docs: list[Path] | None = None,
        source: RepoSource | None = None,
    ) -> None:
        self._local_docs = local_docs or []
        self._source = source

    def detect(self, bin_path: Path) -> Installation | None:
        return None

    def resolve_source(self, inst: Installation) -> RepoSource | None:
        return self._source

    def local_docs(self, inst: Installation) -> list[Path]:
        return self._local_docs


def _installation(
    version: str | None = "1.2.3", root: Path = Path("/root")
) -> Installation:
    return Installation(
        binary="tool",
        bin_path=Path("/bin/tool"),
        real_path=Path("/bin/tool"),
        provider="fake",
        package="tool",
        version=version,
        root=root,
    )


def test_run_install_uses_the_install_root_page_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.install.discovery.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: Path("/installed/tool.1"),
    )

    outcome = run_install("tool")

    assert outcome.tier is Tier.INSTALL_ROOT
    assert "install root" in outcome.detail
    assert "1.2.3" in outcome.detail
    assert "[no synthesis]" in outcome.detail
    assert outcome.installed_path == Path("/installed/tool.1")


def test_run_install_falls_through_to_repository_when_no_install_root_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(local_docs=[], source=source)
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.install.discovery.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpage",
        lambda source, binary, cache_dir=None, config=None, version=None: page,
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: Path("/installed/tool.1"),
    )

    outcome = run_install("tool")

    assert outcome.tier is Tier.REPOSITORY
    assert "repository" in outcome.detail
    assert "1.2.3" in outcome.detail


def test_run_install_tier2_rejects_a_page_naming_a_different_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0016's tier-2 check: a filename match alone is not enough."""
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(local_docs=[], source=source)
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.install.discovery.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    page = tmp_path / "tool.1"
    page.write_text(".TH SOMETHINGELSE 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpage",
        lambda source, binary, cache_dir=None, config=None, version=None: page,
    )

    from maniac.models import PipelineResult

    def _run_pipeline(tool_name: str, **kwargs: object) -> PipelineResult:
        return PipelineResult(
            tool_name=tool_name,
            repo_source=None,
            command_count=1,
            doc_file_count=0,
            context_path=None,
            prompt_path=None,
            markdown_path=tmp_path / f"{tool_name}.1.md",
            roff_path=None,
            installed_path=None,
            markdown_content="# doc",
        )

    monkeypatch.setattr("maniac.orchestration.pipeline.run_pipeline", _run_pipeline)

    outcome = run_install("tool")

    assert outcome.tier is Tier.SYNTHESIS


def test_run_install_tier2_skipped_without_an_installed_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A raw `local_lib` checkout has no version to match a repository tag against."""
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(local_docs=[], source=source)
    inst = _installation(version=None)
    monkeypatch.setattr(
        "maniac.orchestration.install.discovery.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    called = False

    def _discover_repo_manpage(*args: object, **kwargs: object) -> Path:
        nonlocal called
        called = True
        raise AssertionError("tier 2 must not run without an installed version")

    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpage", _discover_repo_manpage
    )

    outcome = run_install("tool", no_generate=True)

    assert not called
    assert outcome.tier is None


def test_generate_flag_skips_tiers_1_and_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation()

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("--generate must not consult tiers 1-2")

    monkeypatch.setattr(
        "maniac.orchestration.install.discovery.find_installation", _fail_if_called
    )

    from maniac.models import PipelineResult

    def _run_pipeline(tool_name: str, **kwargs: object) -> PipelineResult:
        return PipelineResult(
            tool_name=tool_name,
            repo_source=None,
            command_count=1,
            doc_file_count=2,
            context_path=None,
            prompt_path=None,
            markdown_path=tmp_path / f"{tool_name}.1.md",
            roff_path=None,
            installed_path=None,
            markdown_content="# doc",
        )

    monkeypatch.setattr("maniac.orchestration.pipeline.run_pipeline", _run_pipeline)

    outcome = run_install("tool", generate_only=True)

    assert outcome.tier is Tier.SYNTHESIS
    assert "repo docs" in outcome.detail
    del provider, inst  # never consulted; kept only to show intent


def test_no_generate_never_reaches_the_llm_when_no_tier_1_or_2_page_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guarantee ADR-0016 makes for `--no-generate`: falsified by a reachable LLM call.

    Both tiers fail (no provider claims the binary), so a bug that fell
    through to synthesis anyway would call `run_llm_synthesis` -- patched
    here to raise, so the test fails loudly if that path is ever reached,
    rather than only asserting the happy `--no-generate` case succeeds.
    """

    def _explode(*args: object, **kwargs: object) -> str:
        raise AssertionError("an LLM call is reachable under --no-generate")

    monkeypatch.setattr("maniac.generation.llm.run_llm_synthesis", _explode)
    monkeypatch.setattr(
        "maniac.orchestration.install.discovery.find_installation",
        lambda name, bin_dir=None: None,
    )

    outcome = run_install("nonexistent_unknown_tool_xyz", no_generate=True)

    assert outcome.tier is None


def test_no_generate_installs_a_tier_1_page_with_no_llm_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The happy-path pairing: `--no-generate` still does its job when tier 1 answers."""

    def _explode(*args: object, **kwargs: object) -> str:
        raise AssertionError("an LLM call is reachable under --no-generate")

    monkeypatch.setattr("maniac.generation.llm.run_llm_synthesis", _explode)

    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.install.discovery.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: Path("/installed/tool.1"),
    )

    outcome = run_install("tool", no_generate=True)

    assert outcome.tier is Tier.INSTALL_ROOT
