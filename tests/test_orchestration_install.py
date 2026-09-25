"""Tests for `run_install` (ADR-0016): tier selection, `--no-synthesize`."""

from collections import Counter
from pathlib import Path

import pytest
import structlog

from maniac import lifecycle, manifest
from maniac.config import Config
from maniac.installer import InstallResult
from maniac.manifest import Entry
from maniac.models import DocFile, Installation, RepoSource
from maniac.orchestration.context import ResolvedTool
from maniac.orchestration.install import (
    InstallRefused,
    Tier,
    _try_install_root,
    _try_repository,
    run_install,
)
from maniac.sources import pathcache
from maniac.sources.providers.base import Provider, SourceResolver
from maniac.sources.providers.registry import registry

from .manifest_support import record_entry


@pytest.fixture(autouse=True)
def _reachable_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test below names a tool nothing on this machine's `$PATH` has --
    default `which` to "found" so ADR-0061's unreachable-binary refusal
    (tested on its own below) doesn't fire for tests about tier selection
    instead.
    """
    monkeypatch.setattr(pathcache, "which", lambda name: Path(f"/bin/{name}"))


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

    def resolve_source(
        self, inst: Installation, *, config: Config, sources: SourceResolver
    ) -> RepoSource | None:
        return self._source

    def local_docs(self, inst: Installation) -> list[Path]:
        return self._local_docs


def _installation(
    version: str | None = "1.2.3", root: Path = Path("/root"), binary: str = "tool"
) -> Installation:
    return Installation(
        binary=binary,
        bin_path=Path("/bin") / binary,
        real_path=Path("/bin") / binary,
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
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: InstallResult(
            path=Path("/installed/tool.1"), materialized=None, backup_path=None
        ),
    )

    outcome = run_install("tool")

    assert outcome.tier is Tier.INSTALL_ROOT
    assert "install root" in outcome.detail
    assert "1.2.3" in outcome.detail
    assert "[no synthesis]" in outcome.detail
    assert outcome.installed_path == Path("/installed/tool.1")


def test_run_install_dry_run_tier1_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dry-run tier-1 install reports the page it would use without ever
    calling `install_manpage` -- no manpath link, no manifest entry, and
    nothing under `output_dir` or `cache_dir`.
    """
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: pytest.fail("install_manpage reached under dry_run"),
    )
    cfg = Config(
        man_dir=tmp_path / "man1",
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        intermediate_dir=tmp_path / "intermediate",
        manifest_path=tmp_path / "state" / "installed.json",
    )

    outcome = run_install("tool", config=cfg, dry_run=True)

    assert outcome.tier is Tier.INSTALL_ROOT
    assert outcome.installed_path is None
    assert not cfg.man_dir.exists()
    assert not cfg.output_dir.exists()
    assert not cfg.cache_dir.exists()
    assert manifest.load(cfg) == {}


def test_run_install_links_a_verified_install_root_page_directly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "provider" / "tool" / "1.2.3"
    page = root / "share" / "man" / "man1" / "tool.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation(root=root)
    cfg = Config(
        man_dir=tmp_path / "man1",
        output_dir=tmp_path / "maniac",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )

    outcome = run_install("tool", config=cfg)

    entry = manifest.lookup("tool", config=cfg)
    assert outcome.installed_path is not None
    assert outcome.installed_path.resolve() == page
    assert entry is not None
    assert entry.target == page.absolute()
    assert entry.provider_target is True
    assert not cfg.output_dir.exists()


def test_run_install_installs_every_page_of_a_multi_page_install_root_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tier-1 release with a companion page installs both as one unit,
    the primary's manifest key recorded as `group` on each -- the same
    thing tier 2 already does for a multi-page repository release
    (docs/BACKLOG.md, "Install every page of a multi-page install-root
    release").
    """
    root = tmp_path / "provider" / "tool" / "1.2.3"
    primary = root / "share" / "man" / "man1" / "tool.1"
    companion = root / "share" / "man" / "man5" / "tool_colors.5"
    primary.parent.mkdir(parents=True)
    companion.parent.mkdir(parents=True)
    primary.write_text(".TH TOOL 1\n", encoding="utf-8")
    companion.write_text(".TH TOOL_COLORS 5\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[primary, companion])
    inst = _installation(root=root)
    cfg = Config(
        man_dir=tmp_path / "man" / "man1",
        output_dir=tmp_path / "maniac",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )

    outcome = run_install("tool", config=cfg)

    assert outcome.installed_path is not None
    assert outcome.installed_path.resolve() == primary
    companion_path = cfg.man_dir.parent / "man5" / companion.name
    assert companion_path.resolve() == cfg.output_dir / companion.name

    primary_entry = manifest.lookup("tool", config=cfg)
    companion_entry = manifest.lookup("tool_colors", config=cfg)
    assert primary_entry is not None
    assert companion_entry is not None
    assert primary_entry.group == "tool"
    assert companion_entry.group == "tool"
    # The primary links directly to its provider-owned source; the
    # companion, never checked for containment, is copied through the
    # ordinary materialize path instead.
    assert primary_entry.provider_target is True
    assert companion_entry.provider_target is False
    assert companion_entry.target == cfg.output_dir / companion.name


def test_run_install_materializes_an_install_root_page_resolving_outside_its_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "provider" / "tool" / "1.2.3"
    root.mkdir(parents=True)
    external_page = tmp_path / "external" / "tool.1"
    external_page.parent.mkdir()
    external_page.write_text(".TH TOOL 1\n", encoding="utf-8")
    page = root / "share" / "man" / "man1" / "tool.1"
    page.parent.mkdir(parents=True)
    page.symlink_to(external_page)
    provider = _FakeProvider(local_docs=[page])
    inst = _installation(root=root)
    cfg = Config(
        man_dir=tmp_path / "man1",
        output_dir=tmp_path / "maniac",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )

    outcome = run_install("tool", config=cfg)

    entry = manifest.lookup("tool", config=cfg)
    assert outcome.installed_path is not None
    assert outcome.installed_path.resolve() == cfg.output_dir / page.name
    assert entry is not None
    assert entry.provider_target is False


def test_run_install_falls_through_to_repository_when_no_install_root_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(local_docs=[], source=source)
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpages",
        lambda source, binary, cache_dir=None, config=None, version=None: (
            [page],
            True,
        ),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: InstallResult(
            path=Path("/installed/tool.1"), materialized=None, backup_path=None
        ),
    )

    outcome = run_install("tool")

    assert outcome.tier is Tier.REPOSITORY
    assert "repository" in outcome.detail
    assert "1.2.3" in outcome.detail


def test_run_install_dry_run_tier2_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dry-run tier-2 install never opens the manifest transaction that
    installing every page of a release normally opens as one unit.
    """
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(local_docs=[], source=source)
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpages",
        lambda source, binary, cache_dir=None, config=None, version=None: (
            [page],
            True,
        ),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: pytest.fail("install_manpage reached under dry_run"),
    )
    cfg = Config(
        man_dir=tmp_path / "man1",
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        intermediate_dir=tmp_path / "intermediate",
        manifest_path=tmp_path / "state" / "installed.json",
    )

    outcome = run_install("tool", config=cfg, dry_run=True)

    assert outcome.tier is Tier.REPOSITORY
    assert outcome.installed_path is None
    assert not cfg.man_dir.exists()
    assert not cfg.output_dir.exists()
    assert not cfg.cache_dir.exists()
    assert manifest.load(cfg) == {}


# -- _try_install_root: tier 1 in isolation ----------------------------------


def _resolved_tool(
    tmp_path: Path,
    *,
    provider: _FakeProvider | None,
    inst: Installation | None,
    cfg: Config | None = None,
) -> ResolvedTool:
    cfg = cfg or Config(
        man_dir=tmp_path / "man1",
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "output",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    return ResolvedTool(
        tool_name=inst.binary if inst is not None else "tool",
        config=cfg,
        cache_dir=cfg.cache_dir,
        provider=provider,
        installation=inst,
    )


def test_try_install_root_with_no_provider_is_none(tmp_path: Path) -> None:
    tool = _resolved_tool(tmp_path, provider=None, inst=None)

    assert _try_install_root(tool, force=False, dry_run=False) is None


def test_try_install_root_with_no_candidate_is_none(tmp_path: Path) -> None:
    provider = _FakeProvider(local_docs=[])
    tool = _resolved_tool(tmp_path, provider=provider, inst=_installation())

    assert _try_install_root(tool, force=False, dry_run=False) is None


def test_try_install_root_dry_run_reports_the_page_without_installing(
    tmp_path: Path,
) -> None:
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    tool = _resolved_tool(tmp_path, provider=provider, inst=_installation())

    outcome = _try_install_root(tool, force=False, dry_run=True)

    assert outcome is not None
    assert outcome.tier is Tier.INSTALL_ROOT
    assert outcome.installed_path is None
    assert outcome.source_path == page
    assert "[dry run, no synthesis]" in outcome.detail


def test_try_install_root_installs_the_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    tool = _resolved_tool(tmp_path, provider=provider, inst=_installation())
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: InstallResult(
            path=Path("/installed/tool.1"), materialized=None, backup_path=None
        ),
    )

    outcome = _try_install_root(tool, force=False, dry_run=False)

    assert outcome is not None
    assert outcome.tier is Tier.INSTALL_ROOT
    assert outcome.installed_path == Path("/installed/tool.1")
    assert "[no synthesis]" in outcome.detail


# -- _try_repository: tier 2 in isolation ------------------------------------


def test_try_repository_without_an_installed_version_is_none(tmp_path: Path) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    tool = _resolved_tool(tmp_path, provider=provider, inst=_installation(version=None))

    assert _try_repository(tool, force=False, dry_run=False) == (None, True)


def test_try_repository_without_a_documentation_source_is_none(tmp_path: Path) -> None:
    provider = _FakeProvider(source=None)
    tool = _resolved_tool(tmp_path, provider=provider, inst=_installation())

    assert _try_repository(tool, force=False, dry_run=False) == (None, True)


def test_try_repository_with_no_candidate_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    tool = _resolved_tool(tmp_path, provider=provider, inst=_installation())
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpages",
        lambda *args, **kwargs: ([], True),
    )

    assert _try_repository(tool, force=False, dry_run=False) == (None, True)


def test_try_repository_dry_run_reports_the_page_without_installing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    tool = _resolved_tool(tmp_path, provider=provider, inst=_installation())
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpages",
        lambda *args, **kwargs: ([page], True),
    )

    outcome, definitive = _try_repository(tool, force=False, dry_run=True)

    assert outcome is not None
    assert definitive is True
    assert outcome.tier is Tier.REPOSITORY
    assert outcome.installed_path is None
    assert outcome.source_path == page
    assert "[dry run, no synthesis]" in outcome.detail


def test_try_repository_installs_the_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    tool = _resolved_tool(tmp_path, provider=provider, inst=_installation())
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpages",
        lambda *args, **kwargs: ([page], True),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: InstallResult(
            path=Path("/installed/tool.1"), materialized=None, backup_path=None
        ),
    )

    outcome, definitive = _try_repository(tool, force=False, dry_run=False)

    assert outcome is not None
    assert definitive is True
    assert outcome.tier is Tier.REPOSITORY
    assert outcome.installed_path == Path("/installed/tool.1")


def test_run_install_uses_the_exact_tmux_documentation_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = _FakeProvider(
        source=RepoSource(name="tmux", target="tmux/tmux-builds", is_local=False)
    )
    inst = _installation(version="3.7b", binary="tmux")
    page = tmp_path / "tmux.1"
    page.write_text(".TH TMUX 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    observed: list[RepoSource] = []

    def discover(
        source: RepoSource, *args: object, **kwargs: object
    ) -> tuple[list[Path], bool]:
        observed.append(source)
        return [page], True

    monkeypatch.setattr("maniac.orchestration.install.discover_repo_manpages", discover)
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: InstallResult(
            path=Path("/installed/tmux.1"), materialized=None, backup_path=None
        ),
    )

    outcome = run_install("tmux", no_synthesize=True)

    assert outcome.tier is Tier.REPOSITORY
    assert observed == [RepoSource(name="tmux", target="tmux/tmux", is_local=False)]


def test_run_install_tier2_rejects_a_page_naming_a_different_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0016's tier-2 check: a filename match alone is not enough."""
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(local_docs=[], source=source)
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    page = tmp_path / "tool.1"
    page.write_text(".TH SOMETHINGELSE 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpages",
        lambda source, binary, cache_dir=None, config=None, version=None: (
            [page],
            True,
        ),
    )

    from maniac.models import PipelineResult

    def _synthesize(tool: ResolvedTool, **kwargs: object) -> PipelineResult:
        return PipelineResult(
            tool_name=tool.tool_name,
            repo_source=None,
            command_count=1,
            doc_file_count=0,
            context_path=None,
            markdown_path=tmp_path / f"{tool.tool_name}.1.md",
            roff_path=None,
            installed_path=None,
            markdown_content="# doc",
        )

    monkeypatch.setattr("maniac.orchestration.pipeline.synthesize", _synthesize)

    outcome = run_install("tool")

    assert outcome.tier is Tier.SYNTHESIS


def test_run_install_reports_repository_docs_only_synthesis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from maniac.models import PipelineResult

    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.synthesize",
        lambda tool, **kwargs: PipelineResult(
            tool_name=tool.tool_name,
            repo_source=RepoSource(
                name=tool.tool_name, target="owner/tool", is_local=False
            ),
            command_count=0,
            doc_file_count=1,
            context_path=None,
            markdown_path=tmp_path / f"{tool.tool_name}.1.md",
            roff_path=None,
            installed_path=None,
            markdown_content="# doc",
        ),
    )

    outcome = run_install("tool")

    assert outcome.detail == "synthesized from repo docs only"


def test_run_install_names_the_resolved_path_of_an_unclaimed_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0061: running inside a venv now resolves `ruff` to the venv copy
    rather than losing it to the login-`$PATH` refusal -- no installer
    claims it, and the resolved path is logged at `warning` (visible at
    default logging level) so the user can see why.
    """
    from maniac.models import PipelineResult

    venv_ruff = tmp_path / ".venv" / "bin" / "ruff"
    monkeypatch.setattr(pathcache, "which", lambda name: venv_ruff)
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.synthesize",
        lambda tool, **kwargs: PipelineResult(
            tool_name=tool.tool_name,
            repo_source=None,
            command_count=1,
            doc_file_count=0,
            context_path=None,
            markdown_path=tmp_path / f"{tool.tool_name}.1.md",
            roff_path=None,
            installed_path=None,
            markdown_content="# doc",
        ),
    )

    with structlog.testing.capture_logs() as logged:
        run_install("ruff")

    assert {
        "event": "Binary resolves outside any known installer",
        "log_level": "warning",
        "tool": "ruff",
        "resolved_path": str(venv_ruff),
    } in logged


def test_unclaimed_binary_resolved_path_is_visible_at_default_log_level(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`capture_logs()` bypasses structlog's own level filter, so it alone
    cannot prove the resolved path is visible by default (item 5) -- this
    drives the real `setup_logging(verbose=False)` filtering bound logger
    (default WARNING) and checks the rendered line actually reaches stdout.
    """
    from maniac.logging import setup_logging
    from maniac.models import PipelineResult

    venv_ruff = tmp_path / ".venv" / "bin" / "ruff"
    monkeypatch.setattr(pathcache, "which", lambda name: venv_ruff)
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.synthesize",
        lambda tool, **kwargs: PipelineResult(
            tool_name=tool.tool_name,
            repo_source=None,
            command_count=1,
            doc_file_count=0,
            context_path=None,
            markdown_path=tmp_path / f"{tool.tool_name}.1.md",
            roff_path=None,
            installed_path=None,
            markdown_content="# doc",
        ),
    )

    setup_logging(verbose=False)
    run_install("ruff")

    assert str(venv_ruff) in capsys.readouterr().out


def test_run_install_tier2_skipped_without_an_installed_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A raw `local_lib` checkout has no version to match a repository tag against."""
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(local_docs=[], source=source)
    inst = _installation(version=None)
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    called = False

    def _discover_repo_manpages(
        *args: object, **kwargs: object
    ) -> tuple[list[Path], bool]:
        nonlocal called
        called = True
        raise AssertionError("tier 2 must not run without an installed version")

    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpages", _discover_repo_manpages
    )

    outcome = run_install("tool", no_synthesize=True)

    assert not called
    assert outcome.tier is None


def test_run_install_installs_all_anchored_release_manpages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="eza", target="eza-community/eza", is_local=False)
    primary = tmp_path / "eza.1"
    companion = tmp_path / "eza_colors.5"
    primary.write_text(".TH EZA 1\n", encoding="utf-8")
    companion.write_text(".TH EZA_COLORS 5\n", encoding="utf-8")
    cfg = Config(
        cache_dir=tmp_path / "cache",
        man_dir=tmp_path / "man",
        output_dir=tmp_path / "output",
        manifest_path=tmp_path / "state" / "installed.json",
        backup_dir=tmp_path / "state" / "backups",
    )
    cfg.man_dir.mkdir()
    vendor_companion = cfg.man_dir.parent / "man5" / companion.name
    vendor_companion.parent.mkdir()
    vendor_companion.write_text("vendor page\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[], source=source)
    inst = _installation(version="0.23.5", binary="eza")
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpages",
        lambda *args, **kwargs: ([primary, companion], True),
    )

    outcome = run_install("eza", no_synthesize=True, force=True, config=cfg)

    assert outcome.installed_path == cfg.man_dir / primary.name
    assert (cfg.man_dir / primary.name).read_text(encoding="utf-8") == ".TH EZA 1\n"
    companion_path = cfg.man_dir.parent / "man5" / companion.name
    assert companion_path.read_text(encoding="utf-8") == (".TH EZA_COLORS 5\n")
    primary_entry = manifest.lookup("eza", config=cfg)
    assert primary_entry is not None
    assert primary_entry.source == source.target
    companion_entry = manifest.lookup("eza_colors", config=cfg)
    assert companion_entry is not None
    assert companion_entry.source == source.target
    assert companion_entry.path == companion_path
    assert companion_entry.backup == cfg.backup_dir / companion.name
    assert companion_entry.backup.read_text(encoding="utf-8") == "vendor page\n"
    # One release, one uninstallable unit: both pages carry the primary's key.
    assert primary_entry.group == "eza"
    assert companion_entry.group == "eza"


def _release_config(tmp_path: Path) -> Config:
    return Config(
        cache_dir=tmp_path / "cache",
        man_dir=tmp_path / "man" / "man1",
        output_dir=tmp_path / "output",
        manifest_path=tmp_path / "state" / "installed.json",
        backup_dir=tmp_path / "state" / "backups",
    )


def _eza_release(tmp_path: Path) -> list[Path]:
    pages = []
    for name, text in (
        ("eza.1", ".TH EZA 1\n"),
        ("eza_colors.5", ".TH EZA_COLORS 5\n"),
        ("eza_colors-explanation.5", ".TH EZA_COLORS_EXPLANATION 5\n"),
    ):
        page = tmp_path / name
        page.write_text(text, encoding="utf-8")
        pages.append(page)
    return pages


def _resolve_eza_release(monkeypatch: pytest.MonkeyPatch, pages: list[Path]) -> None:
    source = RepoSource(name="eza", target="eza-community/eza", is_local=False)
    provider = _FakeProvider(local_docs=[], source=source)
    inst = _installation(version="0.23.5", binary="eza")
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpages",
        lambda *args, **kwargs: (list(pages), True),
    )


def test_run_install_records_a_release_in_one_manifest_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Three pages, one transaction, one write -- the atomicity ADR-0046 claims."""
    cfg = _release_config(tmp_path)
    _resolve_eza_release(monkeypatch, _eza_release(tmp_path))
    writes = []
    real_save = manifest.save
    monkeypatch.setattr(
        "maniac.manifest.save",
        lambda entries, config=None: (
            writes.append(sorted(entries)),
            real_save(entries, config),
        )[1],
    )

    run_install("eza", no_synthesize=True, config=cfg)

    assert writes == [["eza", "eza_colors", "eza_colors-explanation"]]


def test_run_install_records_no_page_when_a_release_fails_partway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A group naming a primary that was never written is worse than nothing.

    The failure lands on the second of three pages, so the run either
    records the whole release or none of it -- never the prefix that
    reached the manpath before the failure.
    """
    cfg = _release_config(tmp_path)
    _resolve_eza_release(monkeypatch, _eza_release(tmp_path))
    real_link = lifecycle.link_manpath_entry
    linked = Counter()

    def link_once(path: Path, target: Path) -> None:
        linked["calls"] += 1
        if linked["calls"] > 1:
            raise OSError("read-only manpath")
        real_link(path, target)

    monkeypatch.setattr("maniac.lifecycle.link_manpath_entry", link_once)

    with pytest.raises(OSError):
        run_install("eza", no_synthesize=True, config=cfg)

    assert manifest.load(config=cfg) == {}


def test_try_repository_undoes_earlier_pages_when_a_later_page_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0050: the third page's failure must not leave the first two's
    durable targets behind as litter no manifest entry names.

    Before ADR-0050, `eza` and `eza_colors` were fully materialized and
    linked (both `install_manpage` calls succeeded on their own) before
    `eza_colors-explanation` failed; nothing undid them, so their durable
    targets under `output_dir` survived with no manifest entry ever
    recording them -- exactly the litter this fix removes.
    """
    cfg = _release_config(tmp_path)
    _resolve_eza_release(monkeypatch, _eza_release(tmp_path))
    real_link = lifecycle.link_manpath_entry
    linked = Counter()

    def link_twice_then_fail(path: Path, target: Path) -> None:
        linked["calls"] += 1
        if linked["calls"] > 2:
            raise OSError("read-only manpath")
        real_link(path, target)

    monkeypatch.setattr("maniac.lifecycle.link_manpath_entry", link_twice_then_fail)

    with pytest.raises(OSError):
        run_install("eza", no_synthesize=True, config=cfg)

    assert manifest.load(config=cfg) == {}
    # Neither page had a prior entry, so `baseline_entries` names no user of
    # either durable target -- undo must remove both, not just leave them
    # as unrecorded litter (the bug this record closes).  Checking against
    # the loop's *live* entries instead would see each page's own
    # just-`put` record and wrongly treat its target as still in use.
    assert list(cfg.output_dir.rglob("*")) == []


def test_try_repository_undo_leaves_an_unrelated_entrys_target_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The `baseline_entries` trap: a target legitimately used by an entry
    outside this loop must survive the loop's own undo.

    `other`'s entry is recorded before the release's transaction even
    opens, so it is part of `baseline_entries` from the start -- undoing one
    of this loop's own pages must never touch it, whether or not either
    happens to share a target (it does not here; the point is that undo
    only ever inspects the two records it was actually asked to undo).
    """
    cfg = _release_config(tmp_path)
    other_target = cfg.output_dir / "other.1"
    other_target.parent.mkdir(parents=True)
    other_target.write_text("other tool's page\n", encoding="utf-8")
    other_path = cfg.man_dir / "other.1"
    other_path.parent.mkdir(parents=True, exist_ok=True)
    other_path.symlink_to(other_target)
    record_entry(
        "other",
        Entry(
            path=other_path,
            tier=Tier.REPOSITORY,
            source="owner/other",
            checksum=manifest.checksum_of(other_target),
            target=other_target,
        ),
        config=cfg,
    )

    _resolve_eza_release(monkeypatch, _eza_release(tmp_path))
    real_link = lifecycle.link_manpath_entry
    linked = Counter()

    def link_once(path: Path, target: Path) -> None:
        linked["calls"] += 1
        if linked["calls"] > 1:
            raise OSError("read-only manpath")
        real_link(path, target)

    monkeypatch.setattr("maniac.lifecycle.link_manpath_entry", link_once)

    with pytest.raises(OSError):
        run_install("eza", no_synthesize=True, config=cfg)

    assert other_target.read_text(encoding="utf-8") == "other tool's page\n"
    assert manifest.lookup("other", config=cfg) is not None
    assert set(manifest.load(config=cfg)) == {"other"}


def test_try_repository_undo_restores_a_displaced_foreign_pages_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0051: undoing a page whose own call already succeeded must restore
    its backup, not discard it.

    `eza.1` displaces a foreign/vendor page under `--force`, so `eza.1`'s
    `install_manpage` call takes a backup and its own link succeeds cleanly
    -- `dest_file` is now a live symlink, not the pre-call state
    `_restore_or_discard_backup`'s `_path_exists` check assumes.  A later
    page in the same loop (`eza_colors.5`) then fails, and the loop-level
    undo for `eza.1` must put the foreign page's original bytes back rather
    than seeing the (dangling, once its target is discarded) symlink as
    "still occupied" and deleting the one surviving backup -- the regression
    `e2846dc` introduced and ADR-0051 fixes.
    """
    cfg = _release_config(tmp_path)
    _resolve_eza_release(monkeypatch, _eza_release(tmp_path))
    foreign_content = "vendor's own eza.1 page\n"
    foreign_path = cfg.man_dir / "eza.1"
    foreign_path.parent.mkdir(parents=True, exist_ok=True)
    foreign_path.write_text(foreign_content, encoding="utf-8")

    real_link = lifecycle.link_manpath_entry
    linked = Counter()

    def link_once_then_fail(path: Path, target: Path) -> None:
        linked["calls"] += 1
        if linked["calls"] > 1:
            raise OSError("read-only manpath")
        real_link(path, target)

    monkeypatch.setattr("maniac.lifecycle.link_manpath_entry", link_once_then_fail)

    with pytest.raises(OSError):
        run_install("eza", no_synthesize=True, force=True, config=cfg)

    assert manifest.load(config=cfg) == {}
    assert not foreign_path.is_symlink()
    assert foreign_path.read_text(encoding="utf-8") == foreign_content


def test_try_repository_undo_restores_a_version_bumped_pages_prior_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0051's examined-and-accepted gap, now closed: reinstalling over a
    page this same tool already owns must still leave something for a
    sibling's later failure to restore, even though `_take_backup` carries
    `existing.backup` forward unchanged rather than taking a fresh one for
    that case.

    `eza.1` is installed once, then reinstalled with new bytes (a version
    bump reusing its own prior target) as the first page of a second grouped
    install whose later page (`eza_colors.5`) fails.  The loop-level undo for
    `eza.1` must put back the bytes this reinstall was about to overwrite,
    not the (nonexistent) vendor backup, and must not leave the new bytes in
    place -- the residual gap ADR-0051 named and declined to close.
    """
    cfg = _release_config(tmp_path)
    pages = _eza_release(tmp_path)
    _resolve_eza_release(monkeypatch, pages)
    run_install("eza", no_synthesize=True, config=cfg)

    eza_path = cfg.man_dir / "eza.1"
    old_content = eza_path.read_text(encoding="utf-8")
    assert old_content == ".TH EZA 1\n"
    first_entries = manifest.load(config=cfg)

    pages[0].write_text(".TH EZA 1 v2\n", encoding="utf-8")

    real_link = lifecycle.link_manpath_entry
    linked = Counter()

    def link_once_then_fail(path: Path, target: Path) -> None:
        linked["calls"] += 1
        if linked["calls"] > 1:
            raise OSError("read-only manpath")
        real_link(path, target)

    monkeypatch.setattr("maniac.lifecycle.link_manpath_entry", link_once_then_fail)

    with pytest.raises(OSError):
        run_install("eza", no_synthesize=True, config=cfg)

    assert manifest.load(config=cfg) == first_entries
    assert not eza_path.is_symlink()
    assert eza_path.read_text(encoding="utf-8") == old_content
    # No throwaway undo copy left behind once it has done its job.
    assert list(cfg.backup_dir.glob("*.reinstall.tmp")) == []


def test_no_synthesize_never_reaches_the_llm_when_no_tier_1_or_2_page_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guarantee ADR-0016 makes for `--no-synthesize`: falsified by a reachable LLM call.

    Both tiers fail (no provider claims the binary), so a bug that fell
    through to synthesis anyway would call `run_llm_synthesis` -- patched
    here to raise, so the test fails loudly if that path is ever reached,
    rather than only asserting the happy `--no-synthesize` case succeeds.
    """

    def _explode(*args: object, **kwargs: object) -> str:
        raise AssertionError("an LLM call is reachable under --no-synthesize")

    monkeypatch.setattr("maniac.generation.llm.run_llm_synthesis", _explode)
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )

    outcome = run_install("nonexistent_unknown_tool_xyz", no_synthesize=True)

    assert outcome.tier is None


def test_no_synthesize_installs_a_tier_1_page_with_no_llm_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The happy-path pairing: `--no-synthesize` still does its job when tier 1 answers."""

    def _explode(*args: object, **kwargs: object) -> str:
        raise AssertionError("an LLM call is reachable under --no-synthesize")

    monkeypatch.setattr("maniac.generation.llm.run_llm_synthesis", _explode)

    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: InstallResult(
            path=Path("/installed/tool.1"), materialized=None, backup_path=None
        ),
    )

    outcome = run_install("tool", no_synthesize=True)

    assert outcome.tier is Tier.INSTALL_ROOT


def test_run_install_refuses_a_binary_not_on_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0061: a manpage installs globally and permanently, so a binary
    `$PATH` cannot reach at all is refused, and the refusal names why
    rather than declining quietly.
    """
    monkeypatch.setattr(pathcache, "which", lambda name: None)
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )

    with pytest.raises(InstallRefused) as excinfo:
        run_install("project-local-tool")

    message = str(excinfo.value)
    assert "project-local-tool" in message
    assert "$PATH" in message
    assert "global" in message


def test_run_install_refuses_under_no_synthesize_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--no-synthesize` does not buy past the refusal.

    The check asks whether MANIAC should serve this binary at all, which is
    prior to which tier would answer -- so restricting to tiers 1-2 cannot
    reach a binary `$PATH` cannot.
    """
    monkeypatch.setattr(pathcache, "which", lambda name: None)
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )

    with pytest.raises(InstallRefused):
        run_install("project-local-tool", no_synthesize=True)


def test_run_install_refuses_a_project_scoped_mise_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0061: a Mise install active only via a project's config is
    refused outright, naming the tool and the resolved root -- not treated
    as unclaimed and left to fall through to tier-3 synthesis, which would
    document this project's version as the machine's global one."""
    from maniac.exceptions import NotGloballySelected

    root = tmp_path / "installs" / "ripgrep" / "13.0.0"
    bin_path = tmp_path / "bin" / "rg"

    def raise_project_scoped(name: str, bin_dir: str | None = None) -> None:
        raise NotGloballySelected(name, root)

    monkeypatch.setattr(pathcache, "which", lambda name: bin_path)
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        raise_project_scoped,
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: pytest.fail("no tier should run on a refusal"),
    )

    with pytest.raises(InstallRefused) as excinfo:
        run_install("rg")

    message = str(excinfo.value)
    assert "rg" in message
    assert str(root) in message
    assert "project config" in message


def test_run_install_refusal_runs_no_tier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The refusal fires before any tier runs -- even one `find_installation`
    would happily resolve.
    """
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: pytest.fail("no tier should run on a refusal"),
    )
    monkeypatch.setattr(pathcache, "which", lambda name: None)

    with pytest.raises(InstallRefused):
        run_install("tool")


def test_run_install_refuses_synthesis_after_a_non_definitive_repository_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tier-2 probe that fails to complete (network or git error) is not a
    definitive absence: `run_install` must refuse to synthesize over it
    rather than treat the failure the same as a repository genuinely
    checked and found lacking.
    """
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(local_docs=[], source=source)
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpages",
        lambda *args, **kwargs: ([], False),
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands",
        lambda cmd, **kwargs: pytest.fail("synthesis must not run"),
    )

    with pytest.raises(InstallRefused) as excinfo:
        run_install("tool")

    message = str(excinfo.value)
    assert "owner/tool" in message
    assert "tier-2" in message


def test_run_install_no_synthesize_still_works_on_a_non_definitive_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--no-synthesize` never reaches synthesis anyway, so a non-definitive
    tier-2 probe does not change its (already refusal-free) behavior --
    only the path that would otherwise reach `synthesize` gains a refusal.
    """
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(local_docs=[], source=source)
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpages",
        lambda *args, **kwargs: ([], False),
    )

    outcome = run_install("tool", no_synthesize=True)

    assert outcome.tier is None
    assert "tiers 1-2 only" in outcome.detail


def test_run_install_explicit_bin_dir_bypasses_the_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicit `bin_dir` is stated intent (mirrors `resolve_bin_path`'s
    own precedence): reachability is judged there directly, never routed
    through `$PATH` at all.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "tool").touch(mode=0o755)
    monkeypatch.setattr(pathcache, "which", lambda name: None)

    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: InstallResult(
            path=Path("/installed/tool.1"), materialized=None, backup_path=None
        ),
    )

    outcome = run_install("tool", bin_dir=bin_dir)

    assert outcome.tier is Tier.INSTALL_ROOT


def test_run_install_refuses_an_unmanaged_destination_before_any_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A foreign page already at the manpath destination refuses before any
    tier runs -- not just before `install_manpage`'s own `_take_backup`
    check, which only fires after `--help` is crawled, a context snapshot
    is written and an LLM is called. Nothing here should be reached.
    """
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    (man_dir / "tool.1").write_text(".TH TOOL 1 vendor\n", encoding="utf-8")
    cfg = Config(
        man_dir=man_dir,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        intermediate_dir=tmp_path / "intermediate",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: pytest.fail("no resolution should run on a refusal"),
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.synthesize",
        lambda *a, **kw: pytest.fail("no tier should run on a refusal"),
    )

    with pytest.raises(InstallRefused):
        run_install("tool", config=cfg)

    assert not cfg.output_dir.exists()
    assert not cfg.intermediate_dir.exists()
    assert not cfg.cache_dir.exists()
    assert manifest.load(cfg) == {}


def test_run_install_force_bypasses_the_unmanaged_destination_precheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--force` reaches the precheck the same way it reaches `_take_backup`."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    (man_dir / "tool.1").write_text(".TH TOOL 1 vendor\n", encoding="utf-8")
    cfg = Config(
        man_dir=man_dir,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        intermediate_dir=tmp_path / "intermediate",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    provider = _FakeProvider(local_docs=[])
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )

    # No candidate at any tier -- the precheck already ran, so reaching here
    # (rather than raising InstallRefused) is what this test is checking.
    outcome = run_install("tool", config=cfg, force=True, no_synthesize=True)

    assert outcome.tier is None


def test_run_install_does_not_refuse_a_manifest_owned_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reinstalling a tool MANIAC already owns must not trip the precheck --
    only a foreign, unmanaged page at the destination should.
    """
    from maniac.installer import PageRequest, install_manpage
    from maniac.manifest import Tier as ManifestTier

    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    cfg = Config(
        man_dir=man_dir,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        intermediate_dir=tmp_path / "intermediate",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    source = tmp_path / "source" / "tool.1"
    source.parent.mkdir()
    source.write_text(".TH TOOL 1 maniac\n", encoding="utf-8")
    install_manpage(
        source,
        "tool",
        PageRequest(ManifestTier.SYNTHESIS, "model"),
        target_dir=man_dir,
        config=cfg,
    )

    provider = _FakeProvider(local_docs=[])
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )

    outcome = run_install("tool", config=cfg, no_synthesize=True)

    assert outcome.tier is None


def test_run_install_does_not_refuse_its_own_orphaned_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manifest row lost after linking -- a crash between link and record,
    or manifest loss/recovery -- must not make MANIAC's own page read as
    foreign on reinstall. `_adopt_orphans` exists precisely to recognize
    this page (a symlink resolving under `output_dir`) as MANIAC's; the
    precheck must recognize it the same way, not just `manifest.transaction`.
    """
    from maniac.installer import PageRequest, install_manpage
    from maniac.manifest import Tier as ManifestTier

    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    cfg = Config(
        man_dir=man_dir,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        intermediate_dir=tmp_path / "intermediate",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    source = tmp_path / "source" / "tool.1"
    source.parent.mkdir()
    source.write_text(".TH TOOL 1 maniac\n", encoding="utf-8")
    install_manpage(
        source,
        "tool",
        PageRequest(ManifestTier.SYNTHESIS, "model"),
        target_dir=man_dir,
        config=cfg,
    )
    # Simulate a crash between linking and recording: the page and its
    # symlink stay, but the manifest row naming it is gone.
    cfg.manifest_path.unlink()

    provider = _FakeProvider(local_docs=[])
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )

    outcome = run_install("tool", config=cfg, no_synthesize=True)

    assert outcome.tier is None


def test_run_install_refuses_a_dangling_output_dir_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F3: a dangling symlink under `output_dir` is not owned, precheck or not.

    `_entry_from_link` requires `resolved.is_file()` before adopting a link
    as MANIAC's own -- a target that no longer exists proves nothing. The
    precheck must apply the identical rule (`manifest.is_owned_symlink`),
    or it lets a run past the cheap gate that `_adopt_orphans` will refuse
    to adopt moments later, wasting a crawl and an LLM call before
    `_take_backup` finally raises on the same page.
    """
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    output_dir = tmp_path / "out"
    output_dir.mkdir(parents=True)
    dangling_target = output_dir / "tool.1"
    (man_dir / "tool.1").symlink_to(dangling_target)
    assert not dangling_target.exists()

    cfg = Config(
        man_dir=man_dir,
        cache_dir=tmp_path / "cache",
        output_dir=output_dir,
        intermediate_dir=tmp_path / "intermediate",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: pytest.fail("no resolution should run on a refusal"),
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.synthesize",
        lambda *a, **kw: pytest.fail("no tier should run on a refusal"),
    )

    with pytest.raises(InstallRefused):
        run_install("tool", config=cfg)


def test_run_install_tier1_refuses_a_resolved_destination_beyond_the_default_guess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tier 1 can resolve a destination the run-level `<tool>.1` guess never
    checks -- a compressed page here (`tool.1.gz`) -- so a foreign page
    there must still refuse before `install_manpage` runs, not just at
    `_take_backup` deep inside it.
    """
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    (man_dir / "tool.1.gz").write_bytes(b"vendor's own compressed page")
    cfg = Config(
        man_dir=man_dir,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        intermediate_dir=tmp_path / "intermediate",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    page = tmp_path / "tool.1.gz"
    page.write_bytes(b"upstream's own compressed page")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: pytest.fail(
            "install_manpage reached past the widened precheck"
        ),
    )

    with pytest.raises(InstallRefused):
        run_install("tool", config=cfg, no_synthesize=True)


def test_run_install_tier1_force_bypasses_the_widened_precheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    (man_dir / "tool.1.gz").write_bytes(b"vendor's own compressed page")
    cfg = Config(
        man_dir=man_dir,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        intermediate_dir=tmp_path / "intermediate",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    page = tmp_path / "tool.1.gz"
    page.write_bytes(b"upstream's own compressed page")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: InstallResult(
            path=man_dir / "tool.1.gz", materialized=None, backup_path=None
        ),
    )

    outcome = run_install("tool", config=cfg, no_synthesize=True, force=True)

    assert outcome.tier is Tier.INSTALL_ROOT


def test_run_install_tier1_does_not_refuse_an_owned_resolved_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A destination MANIAC already owns at tier 1's resolved name (not the
    default `<tool>.1` guess) must not trip the widened precheck.
    """
    from maniac.installer import PageRequest, install_manpage
    from maniac.manifest import Tier as ManifestTier

    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    cfg = Config(
        man_dir=man_dir,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        intermediate_dir=tmp_path / "intermediate",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    source = tmp_path / "source" / "tool.1.gz"
    source.parent.mkdir()
    source.write_bytes(b"maniac's own compressed page")
    install_manpage(
        source,
        "tool",
        PageRequest(ManifestTier.INSTALL_ROOT, "/root"),
        target_dir=man_dir,
        config=cfg,
    )

    page = tmp_path / "tool.1.gz"
    page.write_bytes(b"upstream's own compressed page")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )

    outcome = run_install("tool", config=cfg, no_synthesize=True)

    assert outcome.tier is Tier.INSTALL_ROOT


def test_run_install_tier2_refuses_a_foreign_companion_destination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Tier 2 installs a whole bundle (ADR-0042, ADR-0046): a foreign page
    at a *companion's* resolved destination -- not just the primary's --
    must refuse the whole group before any page is materialized.
    """
    cfg = _release_config(tmp_path)
    primary = tmp_path / "eza.1"
    companion = tmp_path / "eza_colors.5"
    primary.write_text(".TH EZA 1\n", encoding="utf-8")
    companion.write_text(".TH EZA_COLORS 5\n", encoding="utf-8")
    vendor_companion = cfg.man_dir.parent / "man5" / companion.name
    vendor_companion.parent.mkdir(parents=True)
    vendor_companion.write_text("vendor page\n", encoding="utf-8")

    source = RepoSource(name="eza", target="eza-community/eza", is_local=False)
    provider = _FakeProvider(local_docs=[], source=source)
    inst = _installation(version="0.23.5", binary="eza")
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpages",
        lambda *args, **kwargs: ([primary, companion], True),
    )
    monkeypatch.setattr(
        "maniac.lifecycle.link_manpath_entry",
        lambda path, target: pytest.fail(
            "no page should be linked once the precheck refuses the group"
        ),
    )

    with pytest.raises(InstallRefused):
        run_install("eza", no_synthesize=True, config=cfg)

    assert manifest.load(config=cfg) == {}
    assert vendor_companion.read_text(encoding="utf-8") == "vendor page\n"


def test_run_install_tier2_force_bypasses_the_widened_precheck(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--force` reaches the per-page tier-2 precheck the same way it
    reaches the run-level one (`test_run_install_installs_all_anchored_release_manpages`
    already covers the resulting install end to end).
    """
    cfg = _release_config(tmp_path)
    primary = tmp_path / "eza.1"
    companion = tmp_path / "eza_colors.5"
    primary.write_text(".TH EZA 1\n", encoding="utf-8")
    companion.write_text(".TH EZA_COLORS 5\n", encoding="utf-8")
    vendor_companion = cfg.man_dir.parent / "man5" / companion.name
    vendor_companion.parent.mkdir(parents=True)
    vendor_companion.write_text("vendor page\n", encoding="utf-8")

    source = RepoSource(name="eza", target="eza-community/eza", is_local=False)
    provider = _FakeProvider(local_docs=[], source=source)
    inst = _installation(version="0.23.5", binary="eza")
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpages",
        lambda *args, **kwargs: ([primary, companion], True),
    )

    outcome = run_install("eza", no_synthesize=True, force=True, config=cfg)

    assert outcome.tier is Tier.REPOSITORY
    assert vendor_companion.read_text(encoding="utf-8") == ".TH EZA_COLORS 5\n"


def test_run_install_tier2_does_not_refuse_an_owned_companion_destination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A companion destination MANIAC already owns from a prior install of
    the same release must not trip the per-page tier-2 precheck on a
    reinstall.
    """
    cfg = _release_config(tmp_path)
    pages = _eza_release(tmp_path)
    _resolve_eza_release(monkeypatch, pages)

    first = run_install("eza", no_synthesize=True, config=cfg)
    assert first.tier is Tier.REPOSITORY

    second = run_install("eza", no_synthesize=True, config=cfg)

    assert second.tier is Tier.REPOSITORY


def test_run_install_reinstall_prunes_a_page_the_new_release_no_longer_ships(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A group member absent from a fresh release's pages is pruned (backlog:
    "Prune a release group when its upstream drops a page").

    `eza` ships three pages once, then a later release of the same tool
    (discovered through the same primary) drops `eza_colors-explanation`.
    Reinstalling must forget that page's manifest entry and remove its
    manpath link, the same cleanup `_uninstall_group` gives a removed
    member -- through the real reinstall path, not a hand-built manifest.
    """
    cfg = _release_config(tmp_path)
    pages = _eza_release(tmp_path)
    _resolve_eza_release(monkeypatch, pages)

    run_install("eza", no_synthesize=True, config=cfg)
    dropped_path = cfg.man_dir.parent / "man5" / "eza_colors-explanation.5"
    assert dropped_path.exists()
    assert manifest.lookup("eza_colors-explanation", config=cfg) is not None

    pages.pop()  # upstream's next release drops eza_colors-explanation.5
    outcome = run_install("eza", no_synthesize=True, config=cfg)

    assert outcome.tier is Tier.REPOSITORY
    assert manifest.lookup("eza_colors-explanation", config=cfg) is None
    assert not dropped_path.exists()
    kept_entries = manifest.load(config=cfg)
    assert set(kept_entries) == {"eza", "eza_colors"}
    assert kept_entries["eza"].group == "eza"
    assert kept_entries["eza_colors"].group == "eza"


def test_run_install_reinstall_prunes_a_dropped_page_restoring_its_vendor_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pruned member that had displaced a vendor page gets that page back,
    exactly as `_uninstall_group` restores a removed member's backup.
    """
    cfg = _release_config(tmp_path)
    pages = _eza_release(tmp_path)
    dropped_dest = cfg.man_dir.parent / "man5" / "eza_colors-explanation.5"
    dropped_dest.parent.mkdir(parents=True)
    dropped_dest.write_text("vendor page\n", encoding="utf-8")
    _resolve_eza_release(monkeypatch, pages)

    run_install("eza", no_synthesize=True, force=True, config=cfg)
    assert manifest.lookup("eza_colors-explanation", config=cfg) is not None

    pages.pop()
    run_install("eza", no_synthesize=True, config=cfg)

    assert manifest.lookup("eza_colors-explanation", config=cfg) is None
    assert dropped_dest.read_text(encoding="utf-8") == "vendor page\n"


def test_run_install_reinstall_with_the_same_pages_prunes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A release that still ships everything it shipped before prunes no member."""
    cfg = _release_config(tmp_path)
    pages = _eza_release(tmp_path)
    _resolve_eza_release(monkeypatch, pages)

    run_install("eza", no_synthesize=True, config=cfg)
    before = manifest.load(config=cfg)

    run_install("eza", no_synthesize=True, config=cfg)

    after = manifest.load(config=cfg)
    assert set(after) == set(before) == {"eza", "eza_colors", "eza_colors-explanation"}


def test_run_install_dry_run_tier3_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dry-run tier-3 install still crawls `--help` and fetches docs to
    report what synthesis would work with, but writes nothing under
    `output_dir` or `intermediate_dir`, and never opens a manifest
    transaction -- `install_manpage` is the only call in the synthesis path
    that opens one, and it must never be reached.
    """
    provider = _FakeProvider(local_docs=[])
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpages",
        lambda *args, **kwargs: ([], True),
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands",
        lambda cmd, **kwargs: {"> tool --help": "Usage: tool"},
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.install_manpage",
        lambda *args, **kwargs: pytest.fail("install_manpage reached under dry_run"),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.shutil.which", lambda name: "/usr/bin/pandoc"
    )
    cfg = Config(
        man_dir=tmp_path / "man1",
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        intermediate_dir=tmp_path / "intermediate",
        manifest_path=tmp_path / "state" / "installed.json",
    )

    outcome = run_install("tool", config=cfg, dry_run=True)

    assert outcome.tier is Tier.SYNTHESIS
    assert outcome.installed_path is None
    assert not cfg.man_dir.exists()
    assert not cfg.output_dir.exists()
    assert not cfg.intermediate_dir.exists()
    assert not cfg.cache_dir.exists()
    assert manifest.load(cfg) == {}


def test_run_install_dry_run_tier3_without_pandoc_reports_no_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """F4: a dry run must report the same verdict a real run would reach.

    Without pandoc, a real tier-3 install compiles nothing and lands no
    page, exiting non-zero (ADR-0048). Before this fix, the dry-run branch
    always returned `tier=Tier.SYNTHESIS` regardless of pandoc, so
    `_no_page_installed` read it as success -- the exact case ADR-0048's
    corrections note calls out.
    """
    provider = _FakeProvider(local_docs=[])
    inst = _installation()
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst),
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpages",
        lambda *args, **kwargs: ([], True),
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands",
        lambda cmd, **kwargs: {"> tool --help": "Usage: tool"},
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.install_manpage",
        lambda *args, **kwargs: pytest.fail("install_manpage reached under dry_run"),
    )
    monkeypatch.setattr("maniac.orchestration.install.shutil.which", lambda name: None)
    cfg = Config(
        man_dir=tmp_path / "man1",
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        intermediate_dir=tmp_path / "intermediate",
        manifest_path=tmp_path / "state" / "installed.json",
    )

    outcome = run_install("tool", config=cfg, dry_run=True)

    assert outcome.tier is None
    assert "pandoc" in outcome.detail


def test_install_reaching_tier_3_resolves_the_tool_once_not_twice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The duplicated work one `ResolvedTool` exists to remove.

    `run_install` resolved the installation for tiers 1-2, and tier 3 then
    resolved it again -- and the repository with it -- through
    `discover_repo`. One context now carries both facts through all three
    tiers, so each resolution runs exactly once and `discover_repo` is never
    reached.
    """
    calls: Counter[str] = Counter()
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(local_docs=[], source=source)
    inst = _installation()

    def _find_installation(
        name: str, bin_dir: object = None
    ) -> tuple[_FakeProvider, Installation]:
        calls["find_installation"] += 1
        return provider, inst

    resolve_source = registry.resolve_source

    def _resolve_source(
        installation: Installation, *, config: Config, provider: Provider | None = None
    ) -> RepoSource | None:
        calls["resolve_source"] += 1
        return resolve_source(installation, config=config, provider=provider)

    def _discover_repo(*args: object, **kwargs: object) -> None:
        raise AssertionError("tier 3 resolved the repository a second time")

    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation", _find_installation
    )
    monkeypatch.setattr(registry, "resolve_source", _resolve_source)
    monkeypatch.setattr("maniac.sources.resolution.discover_repo", _discover_repo)
    # Tier 1 finds no page and tier 2 no repository page, so the install
    # falls through to a real (dry-run) tier-3 synthesis.
    monkeypatch.setattr(
        "maniac.orchestration.install.discover_repo_manpages",
        lambda *args, **kwargs: ([], True),
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands",
        lambda cmd, **kwargs: {"> tool --help": "Usage: tool"},
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.shutil.which", lambda name: "/usr/bin/pandoc"
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: (
            [DocFile(rel_path="README.md", content="# Tool")],
            True,
        ),
    )
    cfg = Config(
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        intermediate_dir=tmp_path / "intermediate",
        manifest_path=tmp_path / "state" / "installed.json",
    )

    outcome = run_install("tool", config=cfg, dry_run=True)

    assert outcome.tier is Tier.SYNTHESIS
    assert calls == Counter({"find_installation": 1, "resolve_source": 1})
