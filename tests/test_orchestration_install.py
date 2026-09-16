"""Tests for `run_install` (ADR-0016): tier selection, `--no-synthesize`."""

from collections import Counter
from pathlib import Path

import pytest

from maniac import lifecycle, manifest
from maniac.config import Config
from maniac.models import DocFile, Installation, RepoSource
from maniac.orchestration.context import ResolvedTool
from maniac.orchestration.install import InstallRefused, Tier, run_install
from maniac.sources import loginpath
from maniac.sources.providers.base import Provider, SourceResolver
from maniac.sources.providers.registry import registry


@pytest.fixture(autouse=True)
def _reachable_from_the_login_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test below names a tool nothing on this machine's `$PATH` has --
    default `which_login` to "found" so ADR-0020's new refusal (tested on
    its own below) doesn't fire for tests about tier selection instead.
    """
    monkeypatch.setattr(loginpath, "which_login", lambda name: Path(f"/bin/{name}"))


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
        lambda *args, **kwargs: Path("/installed/tool.1"),
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
        lambda source, binary, cache_dir=None, config=None, version=None: [page],
    )
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: Path("/installed/tool.1"),
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
        lambda source, binary, cache_dir=None, config=None, version=None: [page],
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

    def discover(source: RepoSource, *args: object, **kwargs: object) -> list[Path]:
        observed.append(source)
        return [page]

    monkeypatch.setattr("maniac.orchestration.install.discover_repo_manpages", discover)
    monkeypatch.setattr(
        "maniac.orchestration.install.install_manpage",
        lambda *args, **kwargs: Path("/installed/tmux.1"),
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
        lambda source, binary, cache_dir=None, config=None, version=None: [page],
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

    def _discover_repo_manpages(*args: object, **kwargs: object) -> list[Path]:
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
        lambda *args, **kwargs: [primary, companion],
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
        lambda *args, **kwargs: list(pages),
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
        lambda *args, **kwargs: Path("/installed/tool.1"),
    )

    outcome = run_install("tool", no_synthesize=True)

    assert outcome.tier is Tier.INSTALL_ROOT


def test_run_install_refuses_a_binary_the_login_path_cannot_reach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0020: a manpage installs globally and permanently, so a binary
    reachable only from the current environment is refused, and the refusal
    names why rather than declining quietly.
    """
    monkeypatch.setattr(loginpath, "which_login", lambda name: None)
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )

    with pytest.raises(InstallRefused) as excinfo:
        run_install("project-local-tool")

    message = str(excinfo.value)
    assert "project-local-tool" in message
    assert "login shell" in message
    assert "global" in message


def test_run_install_refuses_under_no_synthesize_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--no-synthesize` does not buy past the refusal.

    The check asks whether MANIAC should serve this binary at all, which is
    prior to which tier would answer -- so restricting to tiers 1-2 cannot
    reach a binary the login `$PATH` cannot.
    """
    monkeypatch.setattr(loginpath, "which_login", lambda name: None)
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )

    with pytest.raises(InstallRefused):
        run_install("project-local-tool", no_synthesize=True)


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
    monkeypatch.setattr(loginpath, "which_login", lambda name: None)

    with pytest.raises(InstallRefused):
        run_install("tool")


def test_run_install_explicit_bin_dir_bypasses_the_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicit `bin_dir` is stated intent (mirrors `resolve_bin_path`'s
    own precedence): reachability is judged there directly, never routed
    through the login `$PATH` at all.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "tool").touch(mode=0o755)
    monkeypatch.setattr(loginpath, "which_login", lambda name: None)

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
        lambda *args, **kwargs: Path("/installed/tool.1"),
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
    from maniac.installer import draft_entry, install_manpage
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
        draft_entry(ManifestTier.SYNTHESIS, "model"),
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
    from maniac.installer import draft_entry, install_manpage
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
        draft_entry(ManifestTier.SYNTHESIS, "model"),
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
        lambda *args, **kwargs: [],
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
        lambda *args, **kwargs: [],
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
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands",
        lambda cmd, **kwargs: {"> tool --help": "Usage: tool"},
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
