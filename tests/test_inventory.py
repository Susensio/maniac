"""Tests for the `list` inventory seam: classification facts, never a terminal.

ADR-0024's stable ordered snapshots and ADR-0025's deferral of the remote
page probe behind local evidence are contracts of `maniac.listing`, so they
are pinned here rather than through rendered output.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest
import structlog

from maniac import manifest
from maniac.config import Config
from maniac.exceptions import MalformedToolMetadata
from maniac.listing import (
    ActionState,
    Candidate,
    LocalClassification,
    PageSource,
    ToolRow,
    classify,
    compute_rows,
)
from maniac.listing.classification import provider_target_freshness
from maniac.listing.inventory import _build_inventory, _with_target_clusters, group_rows
from maniac.listing.upstream import resolve_upstream
from maniac.manifest import Entry, Tier
from maniac.models import Installation, RepoSource
from maniac.sources import discovery
from maniac.sources.packages import ExternalPageFreshness, ExternalPageVerification
from maniac.sources.providers import mise as mise_module
from maniac.sources.providers.base import SourceResolver

from .listing_support import (
    RecordingObserver,
    _candidate,
    _config,
    _FakeProvider,
    _installation,
)
from .manifest_support import record_entry


@pytest.fixture(autouse=True)
def _every_mise_install_is_global(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fabricated mise installs below aren't judged for project-scope here
    (ADR-0061's own tests cover that) -- default every root to globally
    active so `MiseProvider.detect()` behaves as before.
    """
    monkeypatch.setattr(mise_module, "_is_globally_active", lambda root: True)


@pytest.fixture(autouse=True)
def _no_real_man(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test stays off the development machine's real `man` database
    (mirrors `tests/test_compare.py:216`'s monkeypatch of the same function);
    a test that needs `man` to resolve something overrides this itself.
    """
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: None,
    )


def _classification_pair(
    provider: object,
    inst: Installation | None,
    tool: str,
    cfg: Config,
    entries: Any = None,
) -> tuple[ActionState, PageSource]:
    result = classify(_candidate(provider, inst, tool), cfg, entries)
    return result.state, result.source


# -- compute_rows: enumeration and named-tools paths ------------------------


def test_compute_rows_no_args_walks_providers_not_the_manpath(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Enumeration is `resolution.enumerate_installations`; `man` decides state, not the manpath scan."""
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation(root=tmp_path)
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: [(provider, inst)],
    )

    rows = compute_rows(config=_config(tmp_path))

    assert rows == [
        ToolRow(
            tool="tool",
            package="tool",
            provider="fake",
            state=ActionState.AVAILABLE,
            source=PageSource.VENDOR,
            upstream=None,
            page_path=page,
            target_cluster=0,
        )
    ]


def test_compute_rows_reports_a_discovery_error_row_beside_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tool `resolution` could not even detect (ADR-0060) still gets a row,
    and does not drop the tool discovered cleanly alongside it."""
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation(root=tmp_path)

    def fake_enumerate(
        on_start=None,
        on_scan=None,
        on_error: Callable[[str, MalformedToolMetadata], None] | None = None,
    ):
        assert on_error is not None
        on_error("broken", MalformedToolMetadata(Path("/x/y.toml"), "invalid TOML"))
        return [(provider, inst)]

    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations", fake_enumerate
    )

    rows = compute_rows(config=_config(tmp_path))

    by_tool = {row.tool: row for row in rows}
    assert by_tool["tool"].state is ActionState.AVAILABLE
    broken = by_tool["broken"]
    assert broken.state is ActionState.ERROR
    assert broken.error == "/x/y.toml: invalid TOML"
    assert broken.provider == ""
    assert broken.upstream is None


def test_compute_rows_reports_a_resolve_source_error_row_beside_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A malformed metadata file discovered only while resolving the source
    (ADR-0060) still leaves the row's own tool reported, and the rest intact."""

    class _BrokenProvider:
        name = "broken-provider"

        def detect(self, bin_path: Path) -> Installation | None:
            return None

        def resolve_source(self, inst, *, config, sources):
            raise MalformedToolMetadata(Path("/x/pkg.json"), "invalid JSON")

        def local_docs(self, inst: Installation) -> list[Path]:
            return []

    ok_provider = _FakeProvider()
    ok_inst = _installation(binary="ok-tool")
    broken_inst = _installation(binary="broken-tool")

    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: [
            (ok_provider, ok_inst),
            (_BrokenProvider(), broken_inst),
        ],
    )

    rows = compute_rows(config=_config(tmp_path))

    by_tool = {row.tool: row for row in rows}
    assert by_tool["ok-tool"].state is not ActionState.ERROR
    broken = by_tool["broken-tool"]
    assert broken.state is ActionState.ERROR
    assert broken.error == "/x/pkg.json: invalid JSON"


def test_compute_rows_reflects_a_manifest_write_between_invocations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nothing survives across invocations to hold a run to a stale manifest."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    provider = _FakeProvider()
    inst = _installation()
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: [(provider, inst)],
    )

    before = compute_rows(config=cfg)
    record_entry(
        "tool",
        Entry(
            path=installed,
            tier=Tier.INSTALL_ROOT,
            source="src",
            checksum="abc123",
        ),
        config=cfg,
    )
    after = compute_rows(config=cfg)

    assert before[0].source == PageSource.SYSTEM
    assert after[0].source == PageSource.VENDOR


def test_compute_rows_logs_phase_timing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )
    clock = iter(float(value) for value in range(1, 9))
    monkeypatch.setattr("maniac.listing.inventory.monotonic", lambda: next(clock))

    # `structlog.testing.capture_logs` swaps the processor chain in place
    # rather than the module's `logger` proxy attribute -- patching
    # `logger.debug` directly instead froze the proxy's laziness for the
    # rest of the run (`__getattr__` binds a concrete logger the first time
    # `monkeypatch` reads the old value to restore, and restoration then
    # pins that concrete binding forever instead of undoing the patch).
    with structlog.testing.capture_logs() as logged:
        compute_rows(["tool"], config=_config(tmp_path))

    assert logged == [
        {
            "event": "List inventory timing",
            "log_level": "debug",
            "candidates": 1,
            "manifest_seconds": 1.0,
            "inventory_seconds": 1.0,
            "local_seconds": 1.0,
            "upstream_seconds": 0.0,
            "total_seconds": 7.0,
        }
    ]


class _CountingProvider(_FakeProvider):
    """Counts every request for its upstream repository identity."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.resolutions = 0

    def resolve_source(
        self, inst: Installation, *, config: Config, sources: SourceResolver
    ) -> RepoSource | None:
        self.resolutions += 1
        return super().resolve_source(inst, config=config, sources=sources)


def _count_registry_resolutions(
    monkeypatch: pytest.MonkeyPatch, source: RepoSource
) -> list[int]:
    """One-element call counter for `registry.resolve_source`."""
    calls = [0]

    def counted(inst: Installation, **kwargs: Any) -> RepoSource:
        calls[0] += 1
        return source

    monkeypatch.setattr("maniac.listing.upstream.registry.resolve_source", counted)
    return calls


def test_compute_rows_resolves_upstream_for_a_vendor_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An install-root page still names its repository; only the probe is skipped."""
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _CountingProvider(local_docs=[page], source=source)
    inst = _installation(root=tmp_path)
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: [(provider, inst)],
    )
    monkeypatch.setattr(
        "maniac.listing.upstream.discover_repo_manpage",
        lambda *args, **kwargs: pytest.fail("vendor rows must not probe for a page"),
    )
    registry_calls = _count_registry_resolutions(monkeypatch, source)

    row = compute_rows(config=_config(tmp_path))[0]

    assert row.state is ActionState.AVAILABLE
    assert row.source is PageSource.VENDOR
    assert row.upstream is source
    assert registry_calls == [1]


def test_compute_rows_resolves_upstream_for_a_reachable_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A page `man` resolves under the install root also carries its repository."""
    installed = tmp_path / "share" / "man" / "man1" / "tool.1"
    installed.parent.mkdir(parents=True)
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _CountingProvider(source=source)
    inst = _installation(root=tmp_path)
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: [(provider, inst)],
    )
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda command, tool: installed,
    )
    monkeypatch.setattr(
        "maniac.listing.upstream.discover_repo_manpage",
        lambda *args, **kwargs: pytest.fail("reachable rows must not probe for a page"),
    )
    registry_calls = _count_registry_resolutions(monkeypatch, source)

    row = compute_rows(config=_config(tmp_path))[0]

    assert row.state is ActionState.OK
    assert row.source is PageSource.VENDOR
    assert row.upstream is source
    assert registry_calls == [1]


def test_compute_rows_resolves_upstream_for_an_unresolved_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A row with no local page carries both its repository and a probe."""
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _CountingProvider(source=source)
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: [
            (provider, _installation())
        ],
    )
    monkeypatch.setattr(
        "maniac.listing.upstream.discover_repo_manpage",
        lambda *args, **kwargs: (None, True),
    )

    row = compute_rows(config=_config(tmp_path))[0]

    assert row.state is ActionState.MISSING
    assert row.upstream is source
    assert provider.resolutions == 1


def test_compute_rows_recovers_the_uri_for_an_older_repository_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    installed = cfg.man_dir / "tool.1"
    installed.parent.mkdir(parents=True)
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    record_entry(
        "tool",
        Entry(
            path=installed,
            tier=Tier.REPOSITORY,
            source="owner/tool",
            checksum=manifest.checksum_of(installed),
            version="1.2.3",
        ),
        config=cfg,
    )
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    inst = _installation(version="1.2.3")
    cached = tmp_path / "cache" / "tool.1"
    uri = "https://github.com/owner/tool/blob/v1.2.3/man/tool.1"
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: [(provider, inst)],
    )
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda command, tool: installed,
    )
    monkeypatch.setattr(
        "maniac.listing.upstream.discover_repo_manpage",
        lambda *args, **kwargs: (cached, True),
    )
    monkeypatch.setattr(
        "maniac.listing.upstream.discovered_manpage_uri", lambda page: uri
    )

    row = compute_rows(config=cfg)[0]

    assert row.state is ActionState.OK
    assert row.source is PageSource.UPSTREAM
    assert row.managed
    assert row.page_path == installed
    assert row.page_uri == uri


def test_compute_rows_with_tools_is_unfiltered_and_resolves_each_by_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = _FakeProvider(local_docs=[])
    inst = _installation(binary="bash")
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst) if name == "bash" else None,
    )

    rows = compute_rows(["bash", "unknown"], config=_config(tmp_path))

    assert [row.tool for row in rows] == ["bash", "unknown"]
    assert rows[0].state is ActionState.MISSING  # empty local_docs, nothing to resolve
    assert rows[1].state is ActionState.MISSING  # no provider at all
    assert rows[1].package == "unknown"
    assert rows[1].provider == ""


def test_compute_rows_named_tools_are_deduplicated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )

    rows = compute_rows(["uv", "uv"], config=_config(tmp_path))

    assert [row.tool for row in rows] == ["uv"]


def test_compute_rows_without_an_observer_behaves_the_same(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every caller besides the CLI omits the observer and sees the same rows."""
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )

    rows = compute_rows(["uv"], config=_config(tmp_path))

    assert [row.tool for row in rows] == ["uv"]


def test_compute_rows_named_tools_report_row_progress_but_no_discovery_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The `tools` path never calls `resolution.enumerate_installations`, so
    its discovery callbacks must never fire."""
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )
    observer = RecordingObserver()

    compute_rows(["uv", "gh"], config=_config(tmp_path), observer=observer)

    assert observer.row_totals == [2]
    assert observer.row_scans == 2
    assert observer.discovery_totals == []
    assert observer.inventories == []


def test_compute_rows_no_args_threads_both_phases_callbacks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = _FakeProvider()
    inst = _installation()

    def fake_enumerate(on_start, on_scan, on_error=None):
        on_start(5)
        on_scan()
        return [(provider, inst)]

    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations", fake_enumerate
    )
    observer = RecordingObserver()

    compute_rows(config=_config(tmp_path), observer=observer)

    assert observer.discovery_totals == [5]
    assert observer.discovery_scans == 1
    assert observer.row_totals == [1]
    assert observer.row_scans == 1


def test_compute_rows_upgrades_a_versioned_cached_repository_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    cached_dir = cfg.cache_dir / "manpages"
    cached_dir.mkdir(parents=True)
    page = cached_dir / "fzf.1"
    page.write_text(".TH FZF 1\n", encoding="utf-8")
    source = RepoSource(name="fzf", target="junegunn/fzf", is_local=False)
    provider = _FakeProvider(source=source)
    inst = _installation(binary="fzf", version="0.74.3")
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: [(provider, inst)],
    )
    monkeypatch.setattr(
        "maniac.listing.upstream.discover_repo_manpage",
        lambda *args, **kwargs: (page, True),
    )

    rows = compute_rows(config=cfg)

    assert rows[0].state is ActionState.AVAILABLE
    assert rows[0].source is PageSource.UPSTREAM
    assert rows[0].upstream is source
    assert rows[0].page_path == page


def test_local_repository_page_links_to_its_source_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    page = tmp_path / "checkout" / "man" / "tool.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    source = RepoSource(
        name="tool",
        target=f"LOCAL:{page.parents[1]}",
        is_local=True,
        local_path=page.parents[1],
    )
    provider = _FakeProvider(source=source)
    inst = _installation()
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: [(provider, inst)],
    )
    monkeypatch.setattr(
        "maniac.listing.upstream.discover_repo_manpage",
        lambda *args, **kwargs: (page, True),
    )

    row = compute_rows(config=_config(tmp_path))[0]

    assert row.source is PageSource.UPSTREAM
    assert row.page_uri == page.absolute().as_uri()


def test_compute_rows_uses_the_exact_tmux_documentation_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    distribution_source = RepoSource(
        name="tmux", target="tmux/tmux-builds", is_local=False
    )
    provider = _FakeProvider(source=distribution_source)
    inst = _installation(binary="tmux", version="3.7b")
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: [(provider, inst)],
    )
    observed: list[RepoSource] = []

    def discover(
        source: RepoSource, *args: object, **kwargs: object
    ) -> tuple[None, bool]:
        observed.append(source)
        return None, True

    monkeypatch.setattr("maniac.listing.upstream.discover_repo_manpage", discover)

    rows = compute_rows(config=_config(tmp_path))

    assert observed == [RepoSource(name="tmux", target="tmux/tmux", is_local=False)]
    assert rows[0].upstream == observed[0]


def test_compute_rows_keeps_an_offline_upstream_row_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    inst = _installation()
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: [(provider, inst)],
    )
    monkeypatch.setattr(
        "maniac.listing.upstream.discover_repo_manpage",
        lambda *args, **kwargs: (None, True),
    )

    rows = compute_rows(config=_config(tmp_path))

    assert rows[0].state is ActionState.MISSING
    assert rows[0].source is PageSource.NONE
    assert rows[0].upstream is source


def test_compute_rows_keeps_a_single_failed_upstream_probe_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    inst = _installation()
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: [(provider, inst)],
    )

    def fail_probe(*args: object, **kwargs: object) -> tuple[Path, bool]:
        raise OSError("cache unavailable")

    monkeypatch.setattr("maniac.listing.upstream.discover_repo_manpage", fail_probe)

    rows = compute_rows(config=_config(tmp_path))

    assert rows[0].state is ActionState.MISSING
    assert rows[0].source is PageSource.NONE
    assert rows[0].upstream is source


def test_compute_rows_never_probes_an_unversioned_installation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    inst = _installation(version=None)
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: [(provider, inst)],
    )
    monkeypatch.setattr(
        "maniac.listing.upstream.discover_repo_manpage",
        lambda *args, **kwargs: pytest.fail("versionless rows must not probe upstream"),
    )

    rows = compute_rows(config=_config(tmp_path))

    assert rows[0].state is ActionState.MISSING
    assert rows[0].source is PageSource.NONE


def test_compute_rows_bounds_upstream_probes_and_keeps_row_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    installations = [
        (
            _FakeProvider(
                source=RepoSource(
                    name=f"tool{index}", target=f"owner/tool{index}", is_local=False
                )
            ),
            _installation(binary=f"tool{index}"),
        )
        for index in range(12)
    ]
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: installations,
    )
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_discover(
        source: RepoSource, *args: object, **kwargs: object
    ) -> tuple[Path, bool]:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        if source.name == "tool0":
            raise OSError("network unreachable")
        return Path("/page"), True

    monkeypatch.setattr("maniac.listing.upstream.discover_repo_manpage", fake_discover)
    observer = RecordingObserver()

    rows = compute_rows(config=_config(tmp_path), observer=observer)

    assert [row.tool for row in rows] == sorted(f"tool{index}" for index in range(12))
    assert rows[0].source is PageSource.NONE
    assert all(row.source is PageSource.UPSTREAM for row in rows[1:])
    assert max_active <= 8
    assert observer.row_scans == 12


def test_compute_rows_bounds_parallel_local_classification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`man -w`-backed local work has bounded concurrency, not one-row serial I/O."""
    installations = [
        (_FakeProvider(), _installation(binary=f"tool{index}")) for index in range(12)
    ]
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: installations,
    )
    active = 0
    maximum = 0
    lock = threading.Lock()

    def bounded(
        candidate: Candidate, cfg: Config, entries: object
    ) -> tuple[LocalClassification, None]:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return (
            LocalClassification(ActionState.OK, PageSource.SYSTEM, False, None),
            None,
        )

    monkeypatch.setattr("maniac.listing.inventory._classify_and_resolve", bounded)

    rows = compute_rows(config=_config(tmp_path))

    assert [row.tool for row in rows] == sorted(f"tool{index}" for index in range(12))
    assert 1 < maximum <= 8


def test_compute_rows_loads_the_manifest_once_per_invocation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    installations = [
        (_FakeProvider(), _installation(binary=f"tool{index}")) for index in range(3)
    ]
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: installations,
    )
    loads = 0

    def load_once(config: Config) -> dict[str, object]:
        nonlocal loads
        loads += 1
        return {}

    monkeypatch.setattr("maniac.listing.inventory.manifest.load", load_once)
    monkeypatch.setattr(
        "maniac.listing.classification.manifest.lookup",
        lambda *args, **kwargs: pytest.fail("bulk list must use its manifest snapshot"),
    )

    compute_rows(config=_config(tmp_path))

    assert loads == 1


def test_compute_rows_starts_upstream_before_slow_local_work_finishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    installations = [
        (provider, _installation(binary="fast")),
        (provider, _installation(binary="slow")),
    ]
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: installations,
    )
    slow_started = threading.Event()
    release_slow = threading.Event()
    probe_started = threading.Event()

    def blocking(
        candidate: Candidate, cfg: Config, entries: object
    ) -> tuple[LocalClassification, RepoSource]:
        if candidate.tool == "slow":
            slow_started.set()
            assert release_slow.wait(timeout=2)
        return (
            LocalClassification(ActionState.MISSING, PageSource.NONE, False, None),
            source,
        )

    def probe(*args: object, **kwargs: object) -> tuple[Path, bool]:
        probe_started.set()
        return Path("/page"), True

    monkeypatch.setattr("maniac.listing.inventory._classify_and_resolve", blocking)
    monkeypatch.setattr("maniac.listing.upstream.discover_repo_manpage", probe)
    result: list[ToolRow] = []
    worker = threading.Thread(
        target=lambda: result.extend(compute_rows(config=_config(tmp_path)))
    )

    worker.start()
    assert slow_started.wait(timeout=2)
    assert probe_started.wait(timeout=2)
    release_slow.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert all(row.source is PageSource.UPSTREAM for row in result)


def test_compute_rows_ticks_while_a_slow_future_leaves_a_quiet_gap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    installations = [
        (provider, _installation(binary="fast")),
        (provider, _installation(binary="slow")),
    ]
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: installations,
    )
    release_slow = threading.Event()
    idle = threading.Event()

    class _Idle(RecordingObserver):
        def idle(self) -> None:
            super().idle()
            idle.set()

    def blocking(
        candidate: Candidate, cfg: Config, entries: object
    ) -> tuple[LocalClassification, RepoSource]:
        if candidate.tool == "slow":
            assert release_slow.wait(timeout=2)
        return (
            LocalClassification(ActionState.MISSING, PageSource.NONE, False, None),
            source,
        )

    monkeypatch.setattr("maniac.listing.inventory._classify_and_resolve", blocking)
    monkeypatch.setattr(
        "maniac.listing.upstream.discover_repo_manpage",
        lambda *args, **kwargs: (Path("/page"), True),
    )
    worker = threading.Thread(
        target=lambda: compute_rows(config=_config(tmp_path), observer=_Idle())
    )

    worker.start()
    assert idle.wait(timeout=1)
    release_slow.set()
    worker.join(timeout=2)

    assert not worker.is_alive()


def test_compute_rows_deduplicates_identical_upstream_binary_probes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    installations = [
        (_FakeProvider(source=source), _installation(binary="tool")) for _ in range(3)
    ]
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: installations,
    )
    probes = 0

    def discover(*args: object, **kwargs: object) -> tuple[Path, bool]:
        nonlocal probes
        probes += 1
        return Path("/page"), True

    monkeypatch.setattr("maniac.listing.upstream.discover_repo_manpage", discover)

    rows = compute_rows(config=_config(tmp_path))

    assert probes == 1
    assert all(row.source is PageSource.UPSTREAM for row in rows)


def test_deduplicated_probe_publishes_all_siblings_atomically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    installations = [
        (_FakeProvider(source=source), _installation(binary="tool")) for _ in range(3)
    ]
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: installations,
    )
    monkeypatch.setattr(
        "maniac.listing.upstream.discover_repo_manpage",
        lambda *args, **kwargs: (Path("/page"), True),
    )
    observer = RecordingObserver()

    compute_rows(config=_config(tmp_path), observer=observer)

    assert len(observer.upstream_groups) == 1
    snapshot, indexes = observer.upstream_groups[0]
    assert indexes == {0, 1, 2}
    assert all(row.source is PageSource.UPSTREAM for row in snapshot)


def test_streaming_skeleton_waits_for_complete_enumeration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = _FakeProvider()
    inst = _installation()
    skeleton_seen = threading.Event()
    release_enumeration = threading.Event()

    class _Skeleton(RecordingObserver):
        def inventory_ready(self, rows: tuple[ToolRow, ...]) -> None:
            super().inventory_ready(rows)
            skeleton_seen.set()

    def enumerate_installations(on_start=None, on_scan=None, on_error=None):
        assert release_enumeration.wait(timeout=2)
        return [(provider, inst)]

    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        enumerate_installations,
    )
    worker = threading.Thread(
        target=lambda: compute_rows(config=_config(tmp_path), observer=_Skeleton())
    )
    worker.start()
    assert not skeleton_seen.wait(timeout=0.1)
    release_enumeration.set()
    assert skeleton_seen.wait(timeout=2)
    worker.join(timeout=2)
    assert not worker.is_alive()


def test_compute_rows_emits_one_sorted_complete_skeleton(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = _FakeProvider()
    alpha = _installation(binary="alpha")
    beta = _installation(binary="beta")

    def enumerate_installations(on_start=None, on_scan=None, on_error=None):
        return [(provider, beta), (provider, alpha)]

    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        enumerate_installations,
    )
    observer = RecordingObserver()

    rows = compute_rows(config=_config(tmp_path), observer=observer)

    assert [[row.tool for row in snapshot] for snapshot in observer.inventories] == [
        ["alpha", "beta"]
    ]
    assert [row.tool for row in rows] == ["alpha", "beta"]


def test_compute_rows_streaming_local_callbacks_handle_multiple_partial_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A local callback must decide only its completed row's probe status."""
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    installations = [
        (_FakeProvider(source=source), _installation(binary="first")),
        (_FakeProvider(source=source), _installation(binary="second")),
    ]

    def enumerate_installations(on_start=None, on_scan=None, on_error=None):
        return installations

    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        enumerate_installations,
    )
    monkeypatch.setattr(
        "maniac.listing.upstream.discover_repo_manpage",
        lambda *args, **kwargs: (None, True),
    )
    observer = RecordingObserver()

    compute_rows(config=_config(tmp_path), observer=observer)

    assert [(index, pending) for _, index, pending in observer.classified] == [
        (0, True),
        (1, True),
    ]


def test_compute_rows_callbacks_receive_snapshots_after_initial_and_each_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Callbacks expose render-safe copies rather than the worker-owned row list."""
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: [
            (provider, _installation())
        ],
    )
    monkeypatch.setattr(
        "maniac.listing.upstream.discover_repo_manpage",
        lambda *args, **kwargs: (Path("/page"), True),
    )
    observer = RecordingObserver()

    rows = compute_rows(config=_config(tmp_path), observer=observer)

    local_snapshot, pending = observer.local_facts[0]
    upstream_snapshot, indexes = observer.upstream_groups[0]
    assert local_snapshot[0].state is ActionState.MISSING
    assert local_snapshot[0].upstream is source
    assert pending == {0}
    assert upstream_snapshot[0].state is ActionState.AVAILABLE
    assert indexes == {0}
    assert local_snapshot is not rows
    assert upstream_snapshot is not rows


# -- _classify: the four states ----------------------------------------------


def test_classify_available_when_install_root_ships_an_uninstalled_page(
    tmp_path: Path,
) -> None:
    page = tmp_path / "install_root" / "tool.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation()
    cfg = _config(tmp_path)

    assert _classification_pair(provider, inst, "tool", cfg) == (
        ActionState.AVAILABLE,
        PageSource.VENDOR,
    )


def test_classify_missing_when_nothing_resolves_and_install_root_is_empty(
    tmp_path: Path,
) -> None:
    provider = _FakeProvider(local_docs=[])
    inst = _installation()
    cfg = _config(tmp_path)

    assert _classification_pair(provider, inst, "tool", cfg) == (
        ActionState.MISSING,
        PageSource.NONE,
    )


def test_classify_missing_when_no_provider_at_all(tmp_path: Path) -> None:
    cfg = _config(tmp_path)

    assert _classification_pair(None, None, "tool", cfg) == (
        ActionState.MISSING,
        PageSource.NONE,
    )


def test_classify_ok_when_man_resolves_and_nothing_suggests_staleness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    installed = tmp_path / "usr" / "man1" / "tool.1"
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )

    assert _classification_pair(None, None, "tool", cfg) == (
        ActionState.OK,
        PageSource.SYSTEM,
    )


# -- _classify: Source classification for a reachable page ------------------


def test_classify_source_keeps_vendor_provenance_when_manifest_owns_the_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    record_entry(
        "tool",
        Entry(
            path=installed,
            tier=Tier.INSTALL_ROOT,
            source="src",
            checksum="abc123",
        ),
        config=cfg,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )

    assert _classification_pair(None, None, "tool", cfg) == (
        ActionState.OK,
        PageSource.VENDOR,
    )


@pytest.mark.parametrize(
    ("tier", "source"),
    [
        (Tier.INSTALL_ROOT, PageSource.VENDOR),
        (Tier.REPOSITORY, PageSource.UPSTREAM),
        (Tier.SYNTHESIS, PageSource.MANIAC),
    ],
)
def test_managed_page_keeps_content_provenance_separate_from_ownership(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tier: Tier,
    source: PageSource,
) -> None:
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    record_entry(
        "tool",
        Entry(
            path=installed,
            tier=tier,
            source="origin",
            checksum="abc123",
        ),
        config=cfg,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )

    actual = classify(_candidate(None, None, "tool"), cfg)

    assert actual.source is source
    assert actual.managed
    assert actual.page_path == installed


def test_classify_source_is_vendor_when_resolved_page_sits_under_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    root = tmp_path / "install_root"
    installed = root / "share" / "man" / "man1" / "tool.1"
    installed.parent.mkdir(parents=True)
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    inst = _installation(root=root)

    assert _classification_pair(_FakeProvider(), inst, "tool", cfg) == (
        ActionState.OK,
        PageSource.VENDOR,
    )


def test_classify_source_is_unverified_when_resolved_page_is_external(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    installed = tmp_path / "usr" / "share" / "man" / "man1" / "tool.1.gz"
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.verify_external_page",
        lambda page, **kwargs: ExternalPageVerification(
            ExternalPageFreshness.UNVERIFIED, None
        ),
    )
    inst = _installation(root=tmp_path / "install_root")

    assert _classification_pair(_FakeProvider(), inst, "tool", cfg) == (
        ActionState.UNVERIFIED,
        PageSource.SYSTEM,
    )


# -- _classify: same-page matching (compression, symlink) -------------------


def test_classify_managed_page_can_be_compressed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`man -w` may report a compressed path where the manifest recorded an
    uncompressed one; ownership must still be recognized."""
    cfg = _config(tmp_path)
    entry_dir = tmp_path / "man"
    entry_dir.mkdir(parents=True)
    entry_path = entry_dir / "tool.1"
    entry_path.write_text(".TH TOOL 1\n", encoding="utf-8")
    record_entry(
        "tool",
        Entry(
            path=entry_path,
            tier=Tier.INSTALL_ROOT,
            source="src",
            checksum="abc123",
        ),
        config=cfg,
    )
    installed = entry_dir / "tool.1.gz"  # same base page, compressed
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )

    assert _classification_pair(None, None, "tool", cfg) == (
        ActionState.OK,
        PageSource.VENDOR,
    )


def test_classify_managed_page_matched_through_a_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Either side of the comparison may be a symlink; both are resolved first."""
    cfg = _config(tmp_path)
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real_page = real_dir / "tool.1"
    real_page.write_text(".TH TOOL 1\n", encoding="utf-8")
    record_entry(
        "tool",
        Entry(
            path=real_page,
            tier=Tier.INSTALL_ROOT,
            source="src",
            checksum="abc123",
        ),
        config=cfg,
    )

    link_dir = tmp_path / "man" / "man1"
    link_dir.mkdir(parents=True)
    linked_page = link_dir / "tool.1"
    linked_page.symlink_to(real_page)
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: linked_page,
    )

    assert _classification_pair(None, None, "tool", cfg) == (
        ActionState.OK,
        PageSource.VENDOR,
    )


# -- _classify: outdated requires positive evidence --------------------------


def test_classify_outdated_when_recorded_version_differs_from_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    record_entry(
        "tool",
        Entry(
            path=installed,
            tier=Tier.SYNTHESIS,
            source="model",
            checksum="abc123",
            version="1.0.0",
        ),
        config=cfg,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    inst = _installation(version="2.0.0")

    assert _classification_pair(None, inst, "tool", cfg) == (
        ActionState.OUTDATED,
        PageSource.MANIAC,
    )


@pytest.mark.parametrize("replacement_version", [None, "2.0.0"])
def test_classify_outdated_when_mise_latest_alias_drifts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    replacement_version: str | None,
) -> None:
    root = tmp_path / ".local" / "share" / "mise" / "installs" / "tool" / "1.0.0"
    binary = root / "bin" / "tool"
    binary.parent.mkdir(parents=True)
    binary.touch()
    bin_path = tmp_path / ".local" / "bin" / "tool"
    bin_path.parent.mkdir(parents=True)
    bin_path.symlink_to(binary)
    provider = mise_module.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None
    page = root / "share" / "man" / "man1" / "tool.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    latest = root.parent / "latest"
    latest.symlink_to(root.name)
    alias_page = latest / "share" / "man" / "man1" / "tool.1"
    cfg = _config(tmp_path)
    installed = cfg.man_dir / "tool.1"
    installed.parent.mkdir(parents=True)
    installed.symlink_to(alias_page)
    record_entry(
        "tool",
        Entry(
            path=installed,
            tier=Tier.INSTALL_ROOT,
            source=str(root),
            checksum=manifest.checksum_of(alias_page),
            version="1.0.0",
            target=alias_page,
            provider_target=True,
        ),
        config=cfg,
    )
    latest.unlink()
    if replacement_version is not None:
        other_root = root.parent / replacement_version
        other_page = other_root / "share" / "man" / "man1" / "tool.1"
        other_page.parent.mkdir(parents=True)
        other_page.write_text(".TH TOOL 1\n", encoding="utf-8")
        latest.symlink_to(other_root.name)
    system_page = tmp_path / "usr" / "share" / "man" / "man1" / "tool.1"
    system_page.parent.mkdir(parents=True)
    system_page.write_text(".TH TOOL 1 system\n", encoding="utf-8")
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: (
            system_page if replacement_version is None else installed
        ),
    )

    assert _classification_pair(provider, inst, "tool", cfg) == (
        ActionState.OUTDATED,
        PageSource.VENDOR,
    )


def test_classify_outdated_for_an_unaliased_mise_provider_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / ".local" / "share" / "mise" / "installs" / "tool" / "1.0.0"
    binary = root / "bin" / "tool"
    binary.parent.mkdir(parents=True)
    binary.touch()
    bin_path = tmp_path / ".local" / "bin" / "tool"
    bin_path.parent.mkdir(parents=True)
    bin_path.symlink_to(binary)
    provider = mise_module.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None
    page = root / "share" / "man" / "man1" / "tool.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    cfg = _config(tmp_path)
    installed = cfg.man_dir / "tool.1"
    installed.parent.mkdir(parents=True)
    installed.symlink_to(page)
    record_entry(
        "tool",
        Entry(
            path=installed,
            tier=Tier.INSTALL_ROOT,
            source=str(root),
            checksum=manifest.checksum_of(page),
            version="1.0.0",
            target=page,
            provider_target=True,
        ),
        config=cfg,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )

    assert _classification_pair(provider, inst, "tool", cfg) == (
        ActionState.OUTDATED,
        PageSource.VENDOR,
    )


def test_classify_ok_for_a_generic_direct_provider_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "provider" / "tool" / "1.0.0"
    page = root / "share" / "man" / "man1" / "tool.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    cfg = _config(tmp_path)
    installed = cfg.man_dir / "tool.1"
    installed.parent.mkdir(parents=True)
    installed.symlink_to(page)
    record_entry(
        "tool",
        Entry(
            path=installed,
            tier=Tier.INSTALL_ROOT,
            source=str(root),
            checksum=manifest.checksum_of(page),
            version="1.0.0",
            target=page,
            provider_target=True,
        ),
        config=cfg,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )

    assert _classification_pair(
        _FakeProvider(), _installation(root=root, version="1.0.0"), "tool", cfg
    ) == (ActionState.OK, PageSource.VENDOR)


def test_classify_ok_when_mise_latest_and_binary_advance_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / ".local" / "share" / "mise" / "installs" / "tool" / "1.0.0"
    binary = root / "bin" / "tool"
    binary.parent.mkdir(parents=True)
    binary.touch()
    bin_path = tmp_path / ".local" / "bin" / "tool"
    bin_path.parent.mkdir(parents=True)
    bin_path.symlink_to(binary)
    provider = mise_module.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None
    page = root / "share" / "man" / "man1" / "tool.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    latest = root.parent / "latest"
    latest.symlink_to(root.name)
    alias_page = latest / "share" / "man" / "man1" / "tool.1"
    cfg = _config(tmp_path)
    installed = cfg.man_dir / "tool.1"
    installed.parent.mkdir(parents=True)
    installed.symlink_to(alias_page)
    record_entry(
        "tool",
        Entry(
            path=installed,
            tier=Tier.INSTALL_ROOT,
            source=str(root),
            checksum=manifest.checksum_of(alias_page),
            version="1.0.0",
            target=alias_page,
            provider_target=True,
        ),
        config=cfg,
    )
    replacement_root = root.parent / "2.0.0"
    replacement_binary = replacement_root / "bin" / "tool"
    replacement_binary.parent.mkdir(parents=True)
    replacement_binary.touch()
    replacement_page = replacement_root / "share" / "man" / "man1" / "tool.1"
    replacement_page.parent.mkdir(parents=True)
    replacement_page.write_text(".TH TOOL 1 new\n", encoding="utf-8")
    latest.unlink()
    latest.symlink_to(replacement_root.name)
    inst = replace(
        inst, root=replacement_root, real_path=replacement_binary, version="2.0.0"
    )
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )

    assert _classification_pair(provider, inst, "tool", cfg) == (
        ActionState.OK,
        PageSource.VENDOR,
    )


def test_classify_ok_when_entry_records_no_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`local_lib` never reports a version; absent evidence reads `ok`, permanently."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    record_entry(
        "tool",
        Entry(
            path=installed,
            tier=Tier.SYNTHESIS,
            source="model",
            checksum="abc123",
            version=None,
        ),
        config=cfg,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    inst = _installation(version="2.0.0")

    assert _classification_pair(None, inst, "tool", cfg) == (
        ActionState.OK,
        PageSource.MANIAC,
    )


def test_classify_ok_when_installation_version_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    record_entry(
        "tool",
        Entry(
            path=installed,
            tier=Tier.SYNTHESIS,
            source="model",
            checksum="abc123",
            version="1.0.0",
        ),
        config=cfg,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    inst = _installation(version=None)

    assert _classification_pair(None, inst, "tool", cfg) == (
        ActionState.OK,
        PageSource.MANIAC,
    )


def test_classify_outdated_when_unclaimed_binarys_own_version_differs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0020: no `Installation` to compare against, so the binary's own
    `--version` output is asked directly and compared verbatim."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    record_entry(
        "tool",
        Entry(
            path=installed,
            tier=Tier.SYNTHESIS,
            source="model",
            checksum="abc123",
            version="1.0.0",
        ),
        config=cfg,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.get_version", lambda cmd, **kwargs: "2.0.0"
    )

    assert _classification_pair(None, None, "tool", cfg) == (
        ActionState.OUTDATED,
        PageSource.MANIAC,
    )


def test_classify_ok_when_unclaimed_binarys_own_version_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    record_entry(
        "tool",
        Entry(
            path=installed,
            tier=Tier.SYNTHESIS,
            source="model",
            checksum="abc123",
            version="1.0.0",
        ),
        config=cfg,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.get_version", lambda cmd, **kwargs: "1.0.0"
    )

    assert _classification_pair(None, None, "tool", cfg) == (
        ActionState.OK,
        PageSource.MANIAC,
    )


def test_classify_ok_when_unclaimed_binarys_version_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`get_version` returning `None` is absence of evidence, not evidence of
    staleness (ADR-0018's positive-evidence rule) -- never `outdated`."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    record_entry(
        "tool",
        Entry(
            path=installed,
            tier=Tier.SYNTHESIS,
            source="model",
            checksum="abc123",
            version="1.0.0",
        ),
        config=cfg,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.get_version", lambda cmd, **kwargs: None
    )

    assert _classification_pair(None, None, "tool", cfg) == (
        ActionState.OK,
        PageSource.MANIAC,
    )


def test_classify_no_subprocess_for_unowned_or_versionless_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The subprocess `get_version` triggers is gated on being owned, having
    a recorded version, and having no `Installation` -- an unowned page and
    an owned-but-versionless one must never reach it, keeping a full `list`
    walk's cost bounded to the handful of rows that qualify."""

    def _unexpected_call(cmd: list[str], **kwargs: object) -> str | None:
        raise AssertionError(f"get_version must not be called for this row: {cmd}")

    monkeypatch.setattr("maniac.listing.classification.get_version", _unexpected_call)
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)

    # Unowned: `man` resolves it, but no manifest entry claims it.
    unowned = cfg.man_dir / "unowned.1"
    unowned.write_text(".TH UNOWNED 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: unowned,
    )
    assert _classification_pair(None, None, "unowned", cfg) == (
        ActionState.OK,
        PageSource.SYSTEM,
    )

    # Owned, but the entry itself records no version.
    versionless = cfg.man_dir / "versionless.1"
    versionless.write_text(".TH VERSIONLESS 1\n", encoding="utf-8")
    record_entry(
        "versionless",
        Entry(
            path=versionless,
            tier=Tier.SYNTHESIS,
            source="model",
            checksum="abc123",
            version=None,
        ),
        config=cfg,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: versionless,
    )
    assert _classification_pair(None, None, "versionless", cfg) == (
        ActionState.OK,
        PageSource.MANIAC,
    )


def test_classify_unverified_when_external_page_has_no_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """External pages need package-backed evidence before claiming freshness."""
    cfg = _config(tmp_path)
    installed = tmp_path / "usr" / "man1" / "tool.1"
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.verify_external_page",
        lambda page, **kwargs: ExternalPageVerification(
            ExternalPageFreshness.UNVERIFIED, None
        ),
    )
    inst = _installation(version="2.0.0")

    assert _classification_pair(_FakeProvider(), inst, "tool", cfg) == (
        ActionState.UNVERIFIED,
        PageSource.SYSTEM,
    )


@pytest.mark.parametrize(
    ("freshness", "state"),
    [
        (ExternalPageFreshness.MATCH, ActionState.OK),
        (ExternalPageFreshness.MISMATCH, ActionState.OUTDATED),
    ],
)
def test_classify_uses_proven_external_package_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    freshness: ExternalPageFreshness,
    state: ActionState,
) -> None:
    cfg = _config(tmp_path)
    installed = tmp_path / "usr" / "share" / "man" / "man1" / "tool.1"
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.verify_external_page",
        lambda page, **kwargs: ExternalPageVerification(freshness, None),
    )

    assert _classification_pair(_FakeProvider(), _installation(), "tool", cfg) == (
        state,
        PageSource.SYSTEM,
    )


def test_classify_surfaces_the_provable_external_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The owner `verify_external_page` proves rides along even when it does
    not match the candidate's own package -- the live `python3.12-minimal`
    case, where freshness stays `UNVERIFIED` but the owner is still known."""
    cfg = _config(tmp_path)
    installed = tmp_path / "usr" / "share" / "man" / "man1" / "python.1"
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.verify_external_page",
        lambda page, **kwargs: ExternalPageVerification(
            ExternalPageFreshness.UNVERIFIED, "python3.12-minimal"
        ),
    )

    result = classify(_candidate(_FakeProvider(), _installation(), "python"), cfg)

    assert result.state is ActionState.UNVERIFIED
    assert result.source is PageSource.SYSTEM
    assert result.owning_package == "python3.12-minimal"


@pytest.mark.parametrize(
    ("freshness", "state"),
    [
        (ExternalPageFreshness.MATCH, ActionState.OK),
        (ExternalPageFreshness.MISMATCH, ActionState.OUTDATED),
    ],
)
def test_classify_falls_back_to_roff_header_when_dpkg_is_unverified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    freshness: ExternalPageFreshness,
    state: ActionState,
) -> None:
    """A page `dpkg` cannot own -- a non-Debian system, or one `dpkg -S`
    cannot attribute -- still has a shot at a freshness verdict from its own
    `.TH`/`.Dt` header (docs/BACKLOG.md's roff-header-freshness item)."""
    cfg = _config(tmp_path)
    installed = tmp_path / "usr" / "share" / "man" / "man1" / "tool.1"
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.verify_external_page",
        lambda page, **kwargs: ExternalPageVerification(
            ExternalPageFreshness.UNVERIFIED, None
        ),
    )
    monkeypatch.setattr(
        "maniac.listing.classification.verify_page_header",
        lambda page, **kwargs: freshness,
    )

    assert _classification_pair(_FakeProvider(), _installation(), "tool", cfg) == (
        state,
        PageSource.SYSTEM,
    )


def test_classify_roff_fallback_never_sets_owning_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The roff path proves freshness, not ownership -- `owning_package`
    stays whatever dpkg found (here nothing) even on a roff `MATCH`."""
    cfg = _config(tmp_path)
    installed = tmp_path / "usr" / "share" / "man" / "man1" / "tool.1"
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.verify_external_page",
        lambda page, **kwargs: ExternalPageVerification(
            ExternalPageFreshness.UNVERIFIED, None
        ),
    )
    monkeypatch.setattr(
        "maniac.listing.classification.verify_page_header",
        lambda page, **kwargs: ExternalPageFreshness.MATCH,
    )

    result = classify(_candidate(_FakeProvider(), _installation(), "tool"), cfg)

    assert result.state is ActionState.OK
    assert result.owning_package is None


def test_classify_dpkg_match_never_overridden_by_roff_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """dpkg is tried first; the roff verifier is never even consulted once
    dpkg has already proven a `MATCH`/`MISMATCH`."""
    cfg = _config(tmp_path)
    installed = tmp_path / "usr" / "share" / "man" / "man1" / "tool.1"
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.verify_external_page",
        lambda page, **kwargs: ExternalPageVerification(
            ExternalPageFreshness.MATCH, "tool-package"
        ),
    )

    def _unexpected_roff_call(page: Path, **kwargs: object) -> ExternalPageFreshness:
        raise AssertionError("roff fallback must not run when dpkg already matched")

    monkeypatch.setattr(
        "maniac.listing.classification.verify_page_header", _unexpected_roff_call
    )

    assert _classification_pair(_FakeProvider(), _installation(), "tool", cfg) == (
        ActionState.OK,
        PageSource.SYSTEM,
    )


# -- _classify: reversal of ADR-0016's manifest-only rule --------------------


def test_classify_unmanaged_page_hand_placed_in_man_dir_reads_ok_system(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0017's correction still holds -- an unrecorded page in `man_dir`
    is not MANIAC-owned -- but `man` resolving it now reads `ok`, not `missing`."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )

    assert _classification_pair(None, None, "tool", cfg) == (
        ActionState.OK,
        PageSource.SYSTEM,
    )


def test_classify_manifest_entry_with_vanished_file_and_no_man_hit_is_missing(
    tmp_path: Path,
) -> None:
    """A manifest entry recorded before a crash between record and copy (ADR-0017)
    is not enough on its own once `man` is also asked."""
    cfg = _config(tmp_path)
    record_entry(
        "tool",
        Entry(
            path=cfg.man_dir / "tool.1",
            tier=Tier.SYNTHESIS,
            source="model",
            checksum="abc123",
        ),
        config=cfg,
    )

    assert _classification_pair(None, None, "tool", cfg) == (
        ActionState.MISSING,
        PageSource.NONE,
    )


def test_classify_managed_file_present_but_unreachable_by_man_is_not_managed(
    tmp_path: Path,
) -> None:
    """The real behaviour change ADR-0018 makes: a manifest entry whose file
    exists is no longer enough to read `managed`/`ok`/MANIAC -- `man` must
    also resolve it. Absent that, the row falls through to `available` or
    `missing` like any other unreachable page."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    entry_path = cfg.man_dir / "tool.1"
    entry_path.write_text(".TH TOOL 1\n", encoding="utf-8")
    record_entry(
        "tool",
        Entry(
            path=entry_path,
            tier=Tier.INSTALL_ROOT,
            source="src",
            checksum="abc123",
        ),
        config=cfg,
    )
    # find_installed_manpage_path stays patched to None by the autouse fixture:
    # `man` does not resolve this tool at all, despite the manifest entry.

    assert _classification_pair(None, None, "tool", cfg) == (
        ActionState.MISSING,
        PageSource.NONE,
    )

    provider = _FakeProvider(local_docs=[tmp_path / "install_root" / "tool.1"])
    inst = _installation()
    assert _classification_pair(provider, inst, "tool", cfg) == (
        ActionState.AVAILABLE,
        PageSource.VENDOR,
    )


# -- _classify/compute_rows: manifest drift (ADR-0046's structural link scan) -


def test_classify_drift_true_when_manifest_entry_points_to_vanished_target(
    tmp_path: Path,
) -> None:
    """A recorded symlink target that no longer exists is drift, independent
    of the state `man`'s own resolution produces (MISSING here, per the
    autouse fixture that keeps `man` from resolving anything)."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    entry_path = cfg.man_dir / "tool.1"
    target = tmp_path / "output" / "tool.1"
    entry_path.symlink_to(target)
    record_entry(
        "tool",
        Entry(
            path=entry_path,
            tier=Tier.SYNTHESIS,
            source="model",
            checksum="abc123",
            target=target,
        ),
        config=cfg,
    )

    result = classify(_candidate(None, None, "tool"), cfg)

    assert result.drift is True
    assert result.state is ActionState.MISSING


def test_classify_drift_false_when_link_is_sound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A recorded symlink still pointing where it should is not drift."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    target = tmp_path / "output" / "tool.1"
    target.parent.mkdir(parents=True)
    target.write_text(".TH TOOL 1\n", encoding="utf-8")
    entry_path = cfg.man_dir / "tool.1"
    entry_path.symlink_to(target)
    record_entry(
        "tool",
        Entry(
            path=entry_path,
            tier=Tier.SYNTHESIS,
            source="model",
            checksum="abc123",
            target=target,
        ),
        config=cfg,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: entry_path,
    )

    result = classify(_candidate(None, None, "tool"), cfg)

    assert result.drift is False
    assert result.state is ActionState.OK


def test_classify_drift_false_for_pre_target_entry_with_page_present(
    tmp_path: Path,
) -> None:
    """A pre-ADR-0028 entry (no recorded target) with its page still present
    is `UNVERIFIABLE`, not drift -- there is nothing to prove broken."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    record_entry(
        "tool",
        Entry(path=installed, tier=Tier.INSTALL_ROOT, source="src", checksum="abc123"),
        config=cfg,
    )

    result = classify(_candidate(None, None, "tool"), cfg)

    assert result.drift is False


def test_compute_rows_reports_drift_for_a_manifest_entry_with_deleted_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end: a tool a provider detects, whose manifest entry's symlink
    target has been deleted from disk (`output_dir` cleaned by hand, say),
    surfaces as drift on the row `list` actually renders."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    entry_path = cfg.man_dir / "tool.1"
    target = tmp_path / "output" / "tool.1"
    entry_path.symlink_to(target)
    record_entry(
        "tool",
        Entry(
            path=entry_path,
            tier=Tier.SYNTHESIS,
            source="model",
            checksum="abc123",
            target=target,
        ),
        config=cfg,
    )
    provider = _FakeProvider()
    inst = _installation()
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: [(provider, inst)],
    )

    row = compute_rows(config=cfg)[0]

    assert row.drift is True


# -- _resolve_upstream: calls provider.resolve_source uniformly (mise's
# registry fallback included) -- ADR-0018 lifted the offline gate, so this
# now proves resolution still works end-to-end and still degrades to a
# blank Upstream rather than crashing `list` when the network call fails.


def test_resolve_upstream_calls_provider_resolve_source() -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    inst = _installation()

    assert resolve_upstream(_candidate(provider, inst), config=Config()) is source


def test_resolve_upstream_mise_resolves_from_local_config_without_offline_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`list` no longer passes `offline=True` (ADR-0018 lifted); a real
    `MiseProvider` installation with a local config match still resolves
    from it, same as before the gate was lifted.
    """
    mise_dir = tmp_path / "config" / "mise"
    mise_dir.mkdir(parents=True)
    (mise_dir / "config.toml").write_text(
        "[tool_alias]\nripgrep = 'github:private/rg'\n", encoding="utf-8"
    )
    provider = mise_module.MiseProvider()
    inst = Installation(
        binary="rg",
        bin_path=tmp_path / "bin" / "rg",
        real_path=tmp_path / "bin" / "rg",
        provider="mise",
        package="ripgrep",
        version="14.1.0",
        root=tmp_path / "installs" / "ripgrep" / "14.1.0",
    )

    source = resolve_upstream(
        _candidate(provider, inst), config=Config(config_dir=tmp_path / "config")
    )

    assert source == RepoSource(name="rg", target="private/rg", is_local=False)


def test_resolve_upstream_mise_registry_failure_degrades_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No local config match falls through to the mise registry (the gate
    lifted); `urlopen` raising must not surface as a `list` crash --
    `_read_mise_registry_archive` already catches `URLError`/`OSError`, so
    the whole chain degrades to a blank Upstream, proving the property that
    matters now that the network call actually happens.
    """

    def raising_urlopen(*args: object, **kwargs: object) -> None:
        raise URLError("network unreachable")

    monkeypatch.setattr("maniac.sources.discovery.urlopen", raising_urlopen)
    discovery._load_mise_registry.cache_clear()

    provider = mise_module.MiseProvider()
    inst = Installation(
        binary="nufmt",
        bin_path=tmp_path / "bin" / "nufmt",
        real_path=tmp_path / "bin" / "nufmt",
        provider="mise",
        package="cargo-https-github-com-nushell-nufmt",
        version="HEAD",
        root=tmp_path / "installs" / "cargo-https-github-com-nushell-nufmt" / "HEAD",
    )

    assert (
        resolve_upstream(
            _candidate(provider, inst),
            config=Config(config_dir=tmp_path / "empty-config"),
        )
        is None
    )
    discovery._load_mise_registry.cache_clear()


def test_resolve_upstream_none_without_provider_or_installation() -> None:
    assert resolve_upstream(_candidate(), config=Config()) is None


# -- _with_target_clusters (ADR-0049) ------------------------------------------


def _cluster_row(tool: str, package: str = "python") -> ToolRow:
    return ToolRow(
        tool=tool,
        package=package,
        provider="fake",
        state=ActionState.OK,
        source=PageSource.MANIAC,
        upstream=None,
    )


def _installation_at(
    binary: str, real_path: Path, package: str = "python"
) -> Installation:
    return Installation(
        binary=binary,
        bin_path=real_path.parent / binary,
        real_path=real_path,
        provider="fake",
        package=package,
        version="1.2.3",
        root=real_path.parent,
    )


def test_target_clusters_unify_a_symlink_alias(tmp_path: Path) -> None:
    """`python`/`python3` resolving through one symlink layer to the same file
    share a `target_cluster`, no hashing needed."""
    real_path = tmp_path / "python3.14"
    real_path.write_bytes(b"binary")
    candidates = [
        _candidate(inst=_installation_at(name, real_path))
        for name in ("python", "python3")
    ]
    rows = [_cluster_row(name) for name in ("python", "python3")]

    clustered = _with_target_clusters(rows, candidates)

    assert clustered[0].target_cluster == clustered[1].target_cluster


def test_target_clusters_unify_byte_identical_distinct_files(tmp_path: Path) -> None:
    """`pip`/`pip3`/`pip3.14` are three separate `console_scripts` files with
    identical bytes; content hashing proves them one program."""
    content = b"#!/usr/bin/env python\nfrom pip import main\n"
    paths = [tmp_path / name for name in ("pip", "pip3", "pip3.14")]
    for path in paths:
        path.write_bytes(content)
    candidates = [_candidate(inst=_installation_at(path.name, path)) for path in paths]
    rows = [_cluster_row(path.name) for path in paths]

    clustered = _with_target_clusters(rows, candidates)

    assert len({row.target_cluster for row in clustered}) == 1


def test_target_clusters_keep_distinct_content_apart(tmp_path: Path) -> None:
    """Two distinct files with distinct content never merge -- there is no
    evidence, at either rung, that they are one program."""
    pydoc_path = tmp_path / "pydoc3.14"
    pydoc_path.write_bytes(b"pydoc source")
    config_path = tmp_path / "python3.14-config"
    config_path.write_bytes(b"config source")
    candidates = [
        _candidate(inst=_installation_at(path.name, path))
        for path in (pydoc_path, config_path)
    ]
    rows = [_cluster_row(path.name) for path in (pydoc_path, config_path)]

    clustered = _with_target_clusters(rows, candidates)

    assert clustered[0].target_cluster != clustered[1].target_cluster


def test_target_clusters_same_size_different_content_stay_apart(
    tmp_path: Path,
) -> None:
    """Two distinct files that happen to share a size still get hashed, and
    still split -- a shared size is not evidence, only what makes hashing
    worth trying."""
    left_path = tmp_path / "left"
    left_path.write_bytes(b"aaaaaaaaaa")
    right_path = tmp_path / "right"
    right_path.write_bytes(b"bbbbbbbbbb")
    assert left_path.stat().st_size == right_path.stat().st_size
    candidates = [
        _candidate(inst=_installation_at(path.name, path))
        for path in (left_path, right_path)
    ]
    rows = [_cluster_row(path.name) for path in (left_path, right_path)]

    clustered = _with_target_clusters(rows, candidates)

    assert clustered[0].target_cluster != clustered[1].target_cluster


def test_target_clusters_never_hash_singletons_of_distinct_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partition where every singleton's size is unique to it never opens
    and hashes a file -- there is no same-size peer that hashing could ever
    merge it with."""
    pydoc_path = tmp_path / "pydoc3.14"
    pydoc_path.write_bytes(b"short")
    config_path = tmp_path / "python3.14-config"
    config_path.write_bytes(b"much longer content than the other file")
    assert pydoc_path.stat().st_size != config_path.stat().st_size
    candidates = [
        _candidate(inst=_installation_at(path.name, path))
        for path in (pydoc_path, config_path)
    ]
    rows = [_cluster_row(path.name) for path in (pydoc_path, config_path)]
    monkeypatch.setattr(
        "maniac.listing.inventory._content_digest",
        lambda path: pytest.fail(f"hashed {path} despite a unique size"),
    )

    clustered = _with_target_clusters(rows, candidates)

    assert clustered[0].target_cluster != clustered[1].target_cluster


def test_target_clusters_unreadable_file_gets_own_cluster(
    tmp_path: Path,
) -> None:
    """An unreadable file among a same-size group still gets its own cluster
    -- unreadable is unproven, exactly like the `digest is None` case it
    always was."""
    readable_path = tmp_path / "readable"
    readable_path.write_bytes(b"same size")
    unreadable_path = tmp_path / "unreadable"
    unreadable_path.write_bytes(b"same size")
    unreadable_path.chmod(0o000)
    try:
        candidates = [
            _candidate(inst=_installation_at(path.name, path))
            for path in (readable_path, unreadable_path)
        ]
        rows = [_cluster_row(path.name) for path in (readable_path, unreadable_path)]

        clustered = _with_target_clusters(rows, candidates)

        assert clustered[0].target_cluster != clustered[1].target_cluster
    finally:
        unreadable_path.chmod(0o644)


# -- group_rows (ADR-0049) ----------------------------------------------------


def test_group_rows_key_includes_source_and_upstream() -> None:
    """Two rows sharing (provider, package, state) but differing in Source or
    Upstream must never collapse -- the extended key ADR-0018 requires."""
    rows = [
        ToolRow(
            tool="pandoc",
            package="pandoc",
            provider="mise",
            state=ActionState.OK,
            source=PageSource.MANIAC,
            upstream=None,
        ),
        ToolRow(
            tool="pandoc-lua",
            package="pandoc",
            provider="mise",
            state=ActionState.OK,
            source=PageSource.SYSTEM,
            upstream=None,
        ),
    ]

    assert len(group_rows(rows)) == 2


def test_group_rows_siblings_collapse_despite_differing_reposource_name() -> None:
    """Siblings resolving to one repository collapse, though each `RepoSource`
    carries its own binary name.

    Observed live: `pandoc`, `pandoc-lua` and `pandoc-server` all render
    `jgm/pandoc` in Upstream but rendered as three rows, because the group
    key included `RepoSource.name` -- a field the table never shows.
    """
    rows = [
        ToolRow(
            tool=name,
            package="pandoc",
            provider="mise",
            state=ActionState.AVAILABLE,
            source=PageSource.VENDOR,
            upstream=RepoSource(name=name, target="jgm/pandoc", is_local=False),
        )
        for name in ("pandoc", "pandoc-lua", "pandoc-server")
    ]

    grouped = group_rows(rows)
    assert len(grouped) == 1
    assert len(grouped[0]) == 3


def test_group_rows_differing_page_path_still_splits(tmp_path: Path) -> None:
    """A differing page already proves two different things (ADR-0049), even
    when every other key component agrees."""
    rows = [
        ToolRow(
            tool="pandoc",
            package="pandoc",
            provider="mise",
            state=ActionState.AVAILABLE,
            source=PageSource.VENDOR,
            upstream=None,
            page_path=tmp_path / "pandoc.1",
        ),
        ToolRow(
            tool="pandoc-lua",
            package="pandoc",
            provider="mise",
            state=ActionState.AVAILABLE,
            source=PageSource.VENDOR,
            upstream=None,
            page_path=tmp_path / "pandoc-lua.1",
        ),
    ]

    assert len(group_rows(rows)) == 2


def test_group_rows_differing_owning_package_still_splits() -> None:
    """A page's provable owner is part of the key (ADR-0049): two rows
    disagreeing on it must never render one owning-package label for both."""
    rows = [
        ToolRow(
            tool="pydoc3",
            package="python",
            provider="mise",
            state=ActionState.OK,
            source=PageSource.SYSTEM,
            upstream=None,
            owning_package="python3.12",
        ),
        ToolRow(
            tool="python3-config",
            package="python",
            provider="mise",
            state=ActionState.OK,
            source=PageSource.SYSTEM,
            upstream=None,
            owning_package="libpython3.12-dev:amd64",
        ),
    ]

    assert len(group_rows(rows)) == 2


def test_group_rows_differing_target_cluster_still_splits() -> None:
    """Two proven-distinct programs sharing a package must never collapse,
    even absent any other distinguishing field (ADR-0049)."""
    rows = [
        ToolRow(
            tool="idle3",
            package="python",
            provider="mise",
            state=ActionState.MISSING,
            source=PageSource.NONE,
            upstream=None,
            target_cluster=1,
        ),
        ToolRow(
            tool="pip",
            package="python",
            provider="mise",
            state=ActionState.MISSING,
            source=PageSource.NONE,
            upstream=None,
            target_cluster=2,
        ),
    ]

    assert len(group_rows(rows)) == 2


# -- provider_target_freshness: a provider-owned target's own verdict -------


class _FakeDirectPageProvider(_FakeProvider):
    """A `DirectPageProvider`-conforming provider with a fixed verdict."""

    def __init__(self, current: bool, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._current = current

    def direct_page_target(self, inst: Installation, page: Path) -> Path | None:
        return None

    def is_direct_page_target_current(self, inst: Installation, target: Path) -> bool:
        return self._current


def test_provider_target_freshness_with_no_installation_is_none() -> None:
    candidate = _candidate(provider=_FakeDirectPageProvider(True), inst=None)

    assert provider_target_freshness(candidate, Path("/target")) is None


def test_provider_target_freshness_with_no_target_is_none() -> None:
    candidate = _candidate(provider=_FakeDirectPageProvider(True), inst=_installation())

    assert provider_target_freshness(candidate, None) is None


def test_provider_target_freshness_needs_a_direct_page_provider() -> None:
    """A provider that does not implement `DirectPageProvider` never answers."""
    candidate = _candidate(provider=_FakeProvider(), inst=_installation())

    assert provider_target_freshness(candidate, Path("/target")) is None


def test_provider_target_freshness_delegates_to_the_provider() -> None:
    fresh = _candidate(provider=_FakeDirectPageProvider(True), inst=_installation())
    stale = _candidate(provider=_FakeDirectPageProvider(False), inst=_installation())

    assert provider_target_freshness(fresh, Path("/target")) is True
    assert provider_target_freshness(stale, Path("/target")) is False


# -- _build_inventory: named-tools resolution vs. discovery walk ------------


def test_build_inventory_with_no_tools_walks_and_sorts_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_b = _FakeProvider(name="b")
    provider_a = _FakeProvider(name="a")
    inst_b = _installation(binary="bbb")
    inst_a = _installation(binary="aaa")
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: [
            (provider_b, inst_b),
            (provider_a, inst_a),
        ],
    )
    observer = RecordingObserver()

    candidates, discovered = _build_inventory(None, observer)

    assert discovered is True
    assert [c.tool for c in candidates] == ["aaa", "bbb"]
    assert candidates[0].provider is provider_a
    assert candidates[1].provider is provider_b


def test_build_inventory_no_tools_reports_discovery_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_enumerate(on_start, on_scan, on_error=None):
        on_start(1)
        on_scan()
        return [(_FakeProvider(), _installation())]

    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations", fake_enumerate
    )
    observer = RecordingObserver()

    _build_inventory(None, observer)

    assert observer.discovery_totals == [1]
    assert observer.discovery_scans == 1


def test_build_inventory_with_no_tools_keeps_one_broken_tool_and_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool whose own metadata `resolution` found malformed (ADR-0060) still
    gets a row, and never drops the others."""
    provider = _FakeProvider()
    inst = _installation(binary="ok-tool")

    def fake_enumerate(
        on_start=None,
        on_scan=None,
        on_error: Callable[[str, MalformedToolMetadata], None] | None = None,
    ):
        assert on_error is not None
        on_error("broken-tool", MalformedToolMetadata(Path("/x/y.toml"), "bad toml"))
        return [(provider, inst)]

    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations", fake_enumerate
    )

    candidates, discovered = _build_inventory(None, RecordingObserver())

    assert discovered is True
    assert [c.tool for c in candidates] == ["broken-tool", "ok-tool"]
    broken = candidates[0]
    assert broken.provider is None
    assert broken.installation is None
    assert broken.error == "/x/y.toml: bad toml"


def test_build_inventory_with_tools_keeps_a_broken_tool_and_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _FakeProvider()
    inst = _installation(binary="bash")

    def fake_find_installation(name, bin_dir=None):
        if name == "broken":
            raise MalformedToolMetadata(Path("/x/y.toml"), "bad toml")
        return (provider, inst) if name == "bash" else None

    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.find_installation", fake_find_installation
    )

    candidates, discovered = _build_inventory(["bash", "broken"], RecordingObserver())

    assert discovered is False
    assert [c.tool for c in candidates] == ["bash", "broken"]
    assert candidates[1].error == "/x/y.toml: bad toml"


def test_build_inventory_with_tools_resolves_each_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _FakeProvider()
    inst = _installation(binary="bash")
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.find_installation",
        lambda name, bin_dir=None: (provider, inst) if name == "bash" else None,
    )

    candidates, discovered = _build_inventory(["bash", "unknown"], RecordingObserver())

    assert discovered is False
    assert [c.tool for c in candidates] == ["bash", "unknown"]
    assert candidates[0].provider is provider
    assert candidates[0].installation is inst
    assert candidates[1].provider is None
    assert candidates[1].installation is None


def test_build_inventory_with_tools_deduplicates_repeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )

    candidates, discovered = _build_inventory(["uv", "uv"], RecordingObserver())

    assert discovered is False
    assert [c.tool for c in candidates] == ["uv"]


def test_build_inventory_with_tools_never_walks_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None, on_error=None: pytest.fail(
            "named tools must not walk discovery"
        ),
    )

    _build_inventory(["uv"], RecordingObserver())


def test_target_clusters_leave_an_unclaimed_candidate_alone(tmp_path: Path) -> None:
    """A candidate with no `Installation` (ADR-0020's unclaimed case) never
    merges with anything via `target_cluster`."""
    claimed = _candidate(
        inst=_installation(binary="pip", package="python", root=tmp_path)
    )
    unclaimed = _candidate(tool="pip")
    candidates = [claimed, unclaimed]
    rows = [_cluster_row("pip"), _cluster_row("pip")]

    clustered = _with_target_clusters(rows, candidates)

    assert clustered[0].target_cluster != clustered[1].target_cluster
