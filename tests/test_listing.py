"""Tests for `list` (ADR-0018): decisions only, no Rich-output scraping."""

import io
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest
from rich.console import Console
from rich.live_render import LiveRender
from typer.testing import CliRunner

import maniac.cli as cli_module
from maniac import manifest as manifest_module
from maniac.cli import app
from maniac.cli.listing import (
    ActionState,
    PageSource,
    ToolRow,
    _classify,
    _filter_rows,
    _grouped_for_display,
    _ProgressReporter,
    _render_list,
    _resolve_upstream,
    _streaming_table,
    _StreamingList,
    compute_rows,
)
from maniac.config import Config
from maniac.manifest import Tier
from maniac.models import Installation, RepoSource
from maniac.sources import discovery
from maniac.sources.providers import mise as mise_module

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_real_man(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test stays off the development machine's real `man` database
    (mirrors `tests/test_compare.py:216`'s monkeypatch of the same function);
    a test that needs `man` to resolve something overrides this itself.
    """
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: None,
    )


class _FakeProvider:
    """Minimal `Provider` stand-in with per-test-configurable answers."""

    def __init__(
        self,
        name: str = "fake",
        local_docs: list[Path] | None = None,
        source: RepoSource | None = None,
    ) -> None:
        self.name = name
        self._local_docs = local_docs or []
        self._source = source

    def detect(self, bin_path: Path) -> Installation | None:
        return None

    def resolve_source(
        self, inst: Installation, *, config: Config
    ) -> RepoSource | None:
        return self._source

    def local_docs(self, inst: Installation) -> list[Path]:
        return self._local_docs


def _installation(
    binary: str = "tool",
    package: str = "tool",
    version: str | None = "1.2.3",
    root: Path = Path("/root"),
) -> Installation:
    return Installation(
        binary=binary,
        bin_path=Path(f"/bin/{binary}"),
        real_path=Path(f"/bin/{binary}"),
        provider="fake",
        package=package,
        version=version,
        root=root,
    )


def _config(tmp_path: Path) -> Config:
    return Config(man_dir=tmp_path / "man" / "man1", cache_dir=tmp_path / "repos")


# -- compute_rows: enumeration and named-tools paths ------------------------


def test_compute_rows_no_args_walks_providers_not_the_manpath(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Enumeration is `discovery.enumerate_installations`; `man` decides state, not the manpath scan."""
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation(root=tmp_path)
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None, on_found=None: [
            (on_found(provider, inst), (provider, inst))[1]
            if on_found is not None
            else (provider, inst)
        ],
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
        )
    ]


def test_compute_rows_with_tools_is_unfiltered_and_resolves_each_by_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = _FakeProvider(local_docs=[])
    inst = _installation(binary="bash")
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.find_installation",
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
        "maniac.cli.listing.discovery.find_installation",
        lambda name, bin_dir=None: None,
    )

    rows = compute_rows(["uv", "uv"], config=_config(tmp_path))

    assert [row.tool for row in rows] == ["uv"]


def test_compute_rows_on_row_callbacks_are_optional_and_no_op_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every caller besides the CLI (including every other test here) omits
    the callbacks and must see unchanged behaviour."""
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.find_installation",
        lambda name, bin_dir=None: None,
    )

    rows = compute_rows(["uv"], config=_config(tmp_path))

    assert [row.tool for row in rows] == ["uv"]


def test_compute_rows_named_tools_report_row_progress_but_no_discovery_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The `tools` path never calls `discovery.enumerate_installations`, so
    its discovery callbacks must never fire."""
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.find_installation",
        lambda name, bin_dir=None: None,
    )
    row_starts: list[int] = []
    row_scans = 0
    discovery_starts: list[int] = []

    def on_row_start(total: int) -> None:
        row_starts.append(total)

    def on_row_scan() -> None:
        nonlocal row_scans
        row_scans += 1

    compute_rows(
        ["uv", "gh"],
        config=_config(tmp_path),
        on_discovery_start=discovery_starts.append,
        on_row_start=on_row_start,
        on_row_scan=on_row_scan,
    )

    assert row_starts == [2]
    assert row_scans == 2
    assert discovery_starts == []


def test_compute_rows_no_args_threads_both_phases_callbacks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = _FakeProvider()
    inst = _installation()
    discovery_starts: list[int] = []
    row_starts: list[int] = []
    row_scans = 0

    def fake_enumerate(on_start=None, on_scan=None):
        if on_start is not None:
            on_start(5)
        if on_scan is not None:
            on_scan()
        return [(provider, inst)]

    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations", fake_enumerate
    )

    def on_row_start(total: int) -> None:
        row_starts.append(total)

    def on_row_scan() -> None:
        nonlocal row_scans
        row_scans += 1

    compute_rows(
        config=_config(tmp_path),
        on_discovery_start=discovery_starts.append,
        on_discovery_scan=lambda: None,
        on_row_start=on_row_start,
        on_row_scan=on_row_scan,
    )

    assert discovery_starts == [5]
    assert row_starts == [1]
    assert row_scans == 1


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
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None, on_found=None: [
            (on_found(provider, inst), (provider, inst))[1]
            if on_found is not None
            else (provider, inst)
        ],
    )
    monkeypatch.setattr(
        "maniac.cli.listing.discover_repo_manpage", lambda *args, **kwargs: page
    )

    rows = compute_rows(config=cfg)

    assert rows[0].state is ActionState.AVAILABLE
    assert rows[0].source is PageSource.UPSTREAM
    assert rows[0].upstream is source


def test_compute_rows_keeps_an_offline_upstream_row_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    inst = _installation()
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: [(provider, inst)],
    )
    monkeypatch.setattr(
        "maniac.cli.listing.discover_repo_manpage", lambda *args, **kwargs: None
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
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: [(provider, inst)],
    )

    def fail_probe(*args: object, **kwargs: object) -> Path:
        raise OSError("cache unavailable")

    monkeypatch.setattr("maniac.cli.listing.discover_repo_manpage", fail_probe)

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
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: [(provider, inst)],
    )
    monkeypatch.setattr(
        "maniac.cli.listing.discover_repo_manpage",
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
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: installations,
    )
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_discover(source: RepoSource, *args: object, **kwargs: object) -> Path:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        if source.name == "tool0":
            raise OSError("network unreachable")
        return Path("/page")

    monkeypatch.setattr("maniac.cli.listing.discover_repo_manpage", fake_discover)
    completions = 0

    def on_row_scan() -> None:
        nonlocal completions
        completions += 1

    rows = compute_rows(config=_config(tmp_path), on_row_scan=on_row_scan)

    assert [row.tool for row in rows] == sorted(f"tool{index}" for index in range(12))
    assert rows[0].source is PageSource.NONE
    assert all(row.source is PageSource.UPSTREAM for row in rows[1:])
    assert max_active <= 8
    assert completions == 12


def test_compute_rows_bounds_parallel_local_classification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`man -w`-backed local work has bounded concurrency, not one-row serial I/O."""
    installations = [
        (_FakeProvider(), _installation(binary=f"tool{index}")) for index in range(12)
    ]
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: installations,
    )
    active = 0
    maximum = 0
    lock = threading.Lock()

    def classify(
        provider: object,
        inst: object,
        tool: str,
        cfg: Config,
        entries: object,
    ) -> tuple[ActionState, PageSource, None]:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return ActionState.OK, PageSource.SYSTEM, None

    monkeypatch.setattr("maniac.cli.listing._classify_and_resolve", classify)

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
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: installations,
    )
    loads = 0

    def load_once(config: Config) -> dict[str, object]:
        nonlocal loads
        loads += 1
        return {}

    monkeypatch.setattr("maniac.cli.listing.manifest.load", load_once)
    monkeypatch.setattr(
        "maniac.cli.listing.manifest.lookup",
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
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: installations,
    )
    slow_started = threading.Event()
    release_slow = threading.Event()
    probe_started = threading.Event()

    def classify(
        provider: object,
        inst: Installation,
        tool: str,
        cfg: Config,
        entries: object,
    ) -> tuple[ActionState, PageSource, RepoSource]:
        if tool == "slow":
            slow_started.set()
            assert release_slow.wait(timeout=2)
        return ActionState.MISSING, PageSource.NONE, source

    def probe(*args: object, **kwargs: object) -> Path:
        probe_started.set()
        return Path("/page")

    monkeypatch.setattr("maniac.cli.listing._classify_and_resolve", classify)
    monkeypatch.setattr("maniac.cli.listing.discover_repo_manpage", probe)
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


def test_compute_rows_deduplicates_identical_upstream_binary_probes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    installations = [
        (_FakeProvider(source=source), _installation(binary="tool")) for _ in range(3)
    ]
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: installations,
    )
    probes = 0

    def discover(*args: object, **kwargs: object) -> Path:
        nonlocal probes
        probes += 1
        return Path("/page")

    monkeypatch.setattr("maniac.cli.listing.discover_repo_manpage", discover)

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
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: installations,
    )
    monkeypatch.setattr(
        "maniac.cli.listing.discover_repo_manpage",
        lambda *args, **kwargs: Path("/page"),
    )
    published: list[tuple[list[ToolRow], set[int]]] = []

    compute_rows(
        config=_config(tmp_path),
        on_upstream_rows=lambda rows, indexes: published.append((rows, indexes)),
    )

    assert len(published) == 1
    snapshot, indexes = published[0]
    assert indexes == {0, 1, 2}
    assert all(row.source is PageSource.UPSTREAM for row in snapshot)


def test_streaming_list_renders_checking_before_a_blocked_probe_finishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The first Live frame is local truth, never a premature remote miss."""
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    inst = _installation()
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None, on_found=None: [
            (on_found(provider, inst), (provider, inst))[1]
            if on_found is not None
            else (provider, inst)
        ],
    )
    probe_started = threading.Event()
    release_probe = threading.Event()

    def blocked_probe(*args: object, **kwargs: object) -> Path:
        probe_started.set()
        assert release_probe.wait(timeout=2)
        return Path("/page")

    monkeypatch.setattr("maniac.cli.listing.discover_repo_manpage", blocked_probe)

    frames: list[object] = []

    class FakeLive:
        def __init__(self, renderable: object, **kwargs: object) -> None:
            frames.append(renderable)

        def start(self) -> None:
            self.refresh()

        def update(self, renderable: object, *, refresh: bool) -> None:
            frames.append(renderable)

        def refresh(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class FakeReporter:
        def stop(self) -> None:
            return None

    monkeypatch.setattr("maniac.cli.listing.Live", FakeLive)
    buf = io.StringIO()
    renderer = _StreamingList(
        Console(file=buf, force_terminal=True, no_color=True),
        FakeReporter(),
    )
    result: list[ToolRow] = []

    worker = threading.Thread(
        target=lambda: result.extend(
            compute_rows(
                config=_config(tmp_path),
                on_skeleton=renderer.skeleton,
                on_local_row=renderer.local,
                on_upstream_rows=renderer.upstream,
            )
        )
    )
    worker.start()
    assert probe_started.wait(timeout=2)
    assert len(frames) >= 1
    Console(file=buf, force_terminal=True, no_color=True).print(frames[-1])
    initial = buf.getvalue()
    assert "checking…" in initial
    assert "missing" not in initial

    release_probe.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result[0].state is ActionState.AVAILABLE
    assert len(frames) >= 2
    final_buffer = io.StringIO()
    Console(file=final_buffer, force_terminal=True, no_color=True).print(frames[-1])
    final = final_buffer.getvalue()
    assert "available" in final
    assert "checking…" not in final


def test_streaming_skeleton_waits_for_complete_enumeration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = _FakeProvider()
    inst = _installation()
    skeleton_seen = threading.Event()
    release_enumeration = threading.Event()

    def enumerate_installations(on_start=None, on_scan=None, on_found=None):
        assert release_enumeration.wait(timeout=2)
        return [(provider, inst)]

    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations", enumerate_installations
    )
    worker = threading.Thread(
        target=lambda: compute_rows(
            config=_config(tmp_path),
            on_skeleton=lambda rows: skeleton_seen.set(),
        )
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

    def enumerate_installations(on_start=None, on_scan=None, on_found=None):
        return [(provider, beta), (provider, alpha)]

    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations", enumerate_installations
    )
    skeletons: list[list[str]] = []

    rows = compute_rows(
        config=_config(tmp_path),
        on_skeleton=lambda snapshot: skeletons.append([row.tool for row in snapshot]),
    )

    assert skeletons == [["alpha", "beta"]]
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

    def enumerate_installations(on_start=None, on_scan=None, on_found=None):
        return installations

    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations", enumerate_installations
    )
    monkeypatch.setattr(
        "maniac.cli.listing.discover_repo_manpage", lambda *args, **kwargs: None
    )
    local: list[tuple[int, bool]] = []

    compute_rows(
        config=_config(tmp_path),
        on_skeleton=lambda rows: None,
        on_local_row=lambda rows, index, pending: local.append((index, pending)),
    )

    assert local == [(0, True), (1, True)]


def test_streaming_list_throttles_rapid_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshes = 0
    updates = 0

    class FakeLive:
        def __init__(self, renderable: object, **kwargs: object) -> None:
            return None

        def start(self) -> None:
            self.refresh()

        def update(self, renderable: object, *, refresh: bool) -> None:
            nonlocal updates
            assert not refresh
            updates += 1

        def refresh(self) -> None:
            nonlocal refreshes
            refreshes += 1

        def stop(self) -> None:
            self.refresh()

    class FakeReporter:
        def stop(self) -> None:
            return None

    monkeypatch.setattr("maniac.cli.listing.Live", FakeLive)
    monkeypatch.setattr("maniac.cli.listing.monotonic", lambda: 1.0)
    rows = [
        ToolRow("first", "first", "fake", ActionState.MISSING, PageSource.NONE, None),
        ToolRow("last", "last", "fake", ActionState.MISSING, PageSource.NONE, None),
    ]
    renderer = _StreamingList(Console(file=io.StringIO()), FakeReporter())
    renderer.skeleton(rows)
    for _ in range(20):
        renderer.local(rows, 0, False)
    renderer.local(rows, 1, False)
    renderer.stop()

    # Rich refreshes once at start and once at stop. The callbacks coalesce
    # into one final renderable update rather than building one per row.
    assert refreshes == 2
    assert updates == 1


def test_streaming_list_publishes_slow_updates_at_refresh_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshes = 0
    updates = 0

    class FakeLive:
        def __init__(self, renderable: object, **kwargs: object) -> None:
            return None

        def start(self) -> None:
            self.refresh()

        def update(self, renderable: object, *, refresh: bool) -> None:
            nonlocal updates
            assert not refresh
            updates += 1

        def refresh(self) -> None:
            nonlocal refreshes
            refreshes += 1

        def stop(self) -> None:
            self.refresh()

    class FakeReporter:
        def stop(self) -> None:
            return None

    clock = iter((0.0, 0.3, 0.6))
    monkeypatch.setattr("maniac.cli.listing.Live", FakeLive)
    monkeypatch.setattr("maniac.cli.listing.monotonic", lambda: next(clock))
    rows = [
        ToolRow("first", "first", "fake", ActionState.MISSING, PageSource.NONE, None),
        ToolRow("last", "last", "fake", ActionState.MISSING, PageSource.NONE, None),
    ]
    renderer = _StreamingList(Console(file=io.StringIO()), FakeReporter())

    renderer.skeleton(rows)
    renderer.local(rows, 0, True)
    renderer.upstream(rows, {0, 1})
    renderer.stop()

    assert refreshes == 3
    assert updates == 2


def test_streaming_list_keeps_fixed_binary_rows_at_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderables: list[object] = []
    live_options: list[dict[str, object]] = []
    refreshes = 0
    stopped = 0

    class FakeLive:
        def __init__(self, renderable: object, **kwargs: object) -> None:
            renderables.append(renderable)
            live_options.append(kwargs)

        def start(self) -> None:
            self.refresh()

        def update(self, renderable: object, *, refresh: bool) -> None:
            renderables.append(renderable)
            assert not refresh

        def refresh(self) -> None:
            nonlocal refreshes
            refreshes += 1

        def stop(self) -> None:
            nonlocal stopped
            stopped += 1
            self.refresh()

    class FakeReporter:
        def stop(self) -> None:
            return None

    monkeypatch.setattr("maniac.cli.listing.Live", FakeLive)
    monkeypatch.setattr("maniac.cli.listing.monotonic", lambda: 0.0)
    renderer = _StreamingList(Console(file=io.StringIO()), FakeReporter())
    rows = [
        ToolRow("alpha", "alpha", "fake", ActionState.OK, PageSource.SYSTEM, None),
        ToolRow("alpha-sub", "alpha", "fake", ActionState.OK, PageSource.SYSTEM, None),
        ToolRow("beta", "beta", "fake", ActionState.OK, PageSource.SYSTEM, None),
    ]

    renderer.skeleton(rows)
    for index in range(len(rows)):
        renderer.local(rows, index, False)
    renderer.stop()

    def render_text(renderable: object) -> str:
        output = io.StringIO()
        Console(file=output, force_terminal=True, no_color=True).print(renderable)
        return output.getvalue()

    frames = [render_text(renderable) for renderable in renderables]
    final = frames[-1]
    assert "alpha" in final
    assert "alpha-sub" in final
    assert "alpha (2 binaries)" not in final
    assert final.index("alpha") < final.index("alpha-sub") < final.index("beta")
    for frame in frames:
        assert frame.count("alpha-sub") == 1
        assert frame.count("beta") == 1
        assert frame.index("alpha") < frame.index("alpha-sub") < frame.index("beta")
    assert refreshes == 2
    assert stopped == 1
    assert len(live_options) == 1
    assert live_options[0]["screen"] is False
    assert live_options[0]["vertical_overflow"] == "ellipsis"


def test_streaming_list_crops_tall_live_frames_in_an_alternate_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rich must not repaint a table taller than its normal-screen viewport."""
    frames: list[int] = []
    options: list[dict[str, object]] = []

    class FakeLive:
        def __init__(self, renderable: object, **kwargs: object) -> None:
            self.renderable: Any = renderable
            self.screen = kwargs["screen"]
            self.vertical_overflow: Any = kwargs["vertical_overflow"]
            options.append(kwargs)

        def start(self) -> None:
            self.refresh()

        def update(self, renderable: object, *, refresh: bool) -> None:
            self.renderable = renderable

        def refresh(self) -> None:
            renderable = LiveRender(
                self.renderable, vertical_overflow=self.vertical_overflow
            )
            frames.append(len(console.render_lines(renderable, console.options)))

        def stop(self) -> None:
            # Rich's stop path changes the overflow mode, but an alternate screen
            # does not refresh afterwards (the detail that prevents a tall frame).
            self.vertical_overflow = "visible"
            if not self.screen:
                self.refresh()

    class FakeReporter:
        def stop(self) -> None:
            return None

    console = Console(file=io.StringIO(), force_terminal=True, no_color=True, height=6)
    monkeypatch.setattr("maniac.cli.listing.Live", FakeLive)
    rows = [
        ToolRow(name, name, "fake", ActionState.MISSING, PageSource.NONE, None)
        for name in ("alpha", "beta", "gamma")
    ]
    renderer = _StreamingList(console, FakeReporter())

    renderer.skeleton(rows)
    renderer.local(rows, 0, False)
    assert renderer.stop(completed=True)

    assert len(options) == 1
    assert options[0]["screen"] is True
    assert options[0]["vertical_overflow"] == "crop"
    assert frames
    assert all(height <= console.size.height for height in frames)


def test_streaming_table_bounds_tall_live_rows_without_rendering_inventory() -> None:
    rows = [
        ToolRow(
            f"tool-{index}",
            f"tool-{index}",
            "fake",
            ActionState.MISSING,
            PageSource.NONE,
            None,
        )
        for index in range(1000)
    ]

    table = _streaming_table(rows, set(range(len(rows))), maximum_rows=10)

    assert len(table.rows) == 11
    assert str(table.columns[0]._cells[-1]) == "… 990 more tools"
    assert str(table.columns[3]._cells[-1]) == "full table after completion"


def test_cli_tall_streaming_prints_one_complete_table_after_alt_screen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = io.StringIO()
    target_console = Console(
        file=output, force_terminal=True, no_color=True, height=6, width=80
    )
    rows = [
        ToolRow(name, name, "fake", ActionState.MISSING, PageSource.NONE, None)
        for name in ("alpha", "beta", "gamma")
    ]
    live_options: list[dict[str, object]] = []

    class FakeLive:
        def __init__(self, renderable: object, **kwargs: object) -> None:
            live_options.append(kwargs)

        def start(self) -> None:
            return None

        def update(self, renderable: object, *, refresh: bool) -> None:
            return None

        def refresh(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class FakeReporter:
        def __init__(self, target_console: Console) -> None:
            return None

        def on_phase_start(self, total: int) -> None:
            return None

        def on_scan(self) -> None:
            return None

        def stop(self) -> None:
            return None

    def finished_rows(*args: object, **kwargs: Any) -> list[ToolRow]:
        kwargs["on_skeleton"](rows)
        for index in range(len(rows)):
            kwargs["on_local_row"](rows, index, False)
        return rows

    monkeypatch.setattr(cli_module.console, "_instance", target_console)
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))
    monkeypatch.setattr("maniac.cli.listing.Live", FakeLive)
    monkeypatch.setattr("maniac.cli.listing._ProgressReporter", FakeReporter)
    monkeypatch.setattr("maniac.cli.listing.compute_rows", finished_rows)

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert len(live_options) == 1
    assert live_options[0]["screen"] is True
    assert live_options[0]["vertical_overflow"] == "crop"
    final = output.getvalue()
    assert final.count("Manpage Reachability") == 1
    assert "checking…" not in final
    assert final.index("alpha") < final.index("beta") < final.index("gamma")


def test_cli_tall_streaming_error_leaves_no_incomplete_normal_screen_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = io.StringIO()
    target_console = Console(
        file=output, force_terminal=True, no_color=True, height=6, width=80
    )
    rows = [
        ToolRow(name, name, "fake", ActionState.MISSING, PageSource.NONE, None)
        for name in ("alpha", "beta", "gamma")
    ]
    stopped = 0

    class FakeLive:
        def __init__(self, renderable: object, **kwargs: object) -> None:
            assert kwargs["screen"] is True

        def start(self) -> None:
            return None

        def update(self, renderable: object, *, refresh: bool) -> None:
            return None

        def refresh(self) -> None:
            return None

        def stop(self) -> None:
            nonlocal stopped
            stopped += 1

    class FakeReporter:
        def __init__(self, target_console: Console) -> None:
            return None

        def on_phase_start(self, total: int) -> None:
            return None

        def on_scan(self) -> None:
            return None

        def stop(self) -> None:
            return None

    def failed_rows(*args: object, **kwargs: Any) -> list[ToolRow]:
        kwargs["on_skeleton"](rows)
        raise RuntimeError("probe failed")

    monkeypatch.setattr(cli_module.console, "_instance", target_console)
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))
    monkeypatch.setattr("maniac.cli.listing.Live", FakeLive)
    monkeypatch.setattr("maniac.cli.listing._ProgressReporter", FakeReporter)
    monkeypatch.setattr("maniac.cli.listing.compute_rows", failed_rows)

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    assert stopped == 1
    assert output.getvalue() == ""


def test_streaming_table_keeps_column_geometry_for_long_upstreams() -> None:
    rows = [
        ToolRow(
            "long-tool-name",
            "long-tool-name",
            "fake",
            ActionState.MISSING,
            PageSource.NONE,
            None,
        ),
        ToolRow("short", "short", "fake", ActionState.MISSING, PageSource.NONE, None),
    ]
    checking = _streaming_table(rows, {0, 1})
    resolved_rows = [
        ToolRow(
            "long-tool-name",
            "long-tool-name",
            "fake",
            ActionState.AVAILABLE,
            PageSource.UPSTREAM,
            RepoSource(
                name="long-tool-name",
                target="owner/" + "very-long-upstream-name-" * 8,
                is_local=False,
            ),
        ),
        rows[1],
    ]
    resolved = _streaming_table(resolved_rows, {1})

    checking_columns = checking.columns
    resolved_columns = resolved.columns
    assert [
        (column.width, column.ratio, column.no_wrap, column.overflow)
        for column in checking_columns
    ] == [
        (column.width, column.ratio, column.no_wrap, column.overflow)
        for column in resolved_columns
    ]
    assert checking_columns[0].width == len("long-tool-name")
    assert checking_columns[1].width == len("checking…")
    assert checking_columns[2].width == len("upstream")
    assert checking_columns[3].ratio == 1

    output = io.StringIO()
    Console(file=output, force_terminal=True, no_color=True, width=60).print(resolved)
    assert "…" in output.getvalue()


def test_streaming_list_keeps_provisional_rows_when_computation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderables: list[object] = []

    class FakeLive:
        def __init__(self, renderable: object, **kwargs: object) -> None:
            renderables.append(renderable)

        def start(self) -> None:
            return None

        def update(self, renderable: object, *, refresh: bool) -> None:
            renderables.append(renderable)

        def refresh(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class FakeReporter:
        def stop(self) -> None:
            return None

    monkeypatch.setattr("maniac.cli.listing.Live", FakeLive)
    renderer = _StreamingList(Console(file=io.StringIO()), FakeReporter())
    rows = [
        ToolRow("alpha", "alpha", "fake", ActionState.MISSING, PageSource.NONE, None),
        ToolRow(
            "alpha-sub", "alpha", "fake", ActionState.MISSING, PageSource.NONE, None
        ),
    ]

    renderer.skeleton(rows)
    renderer.stop()

    output = io.StringIO()
    Console(file=output, force_terminal=True, no_color=True).print(renderables[-1])
    final = output.getvalue()
    assert "checking…" in final
    assert "alpha-sub" in final
    assert "alpha (2 binaries)" not in final


def test_compute_rows_callbacks_receive_snapshots_after_initial_and_each_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Callbacks expose render-safe copies rather than the worker-owned row list."""
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: [(provider, _installation())],
    )
    monkeypatch.setattr(
        "maniac.cli.listing.discover_repo_manpage",
        lambda *args, **kwargs: Path("/page"),
    )
    initial: list[list[ToolRow]] = []
    finalized: list[tuple[list[ToolRow], int]] = []

    rows = compute_rows(
        config=_config(tmp_path),
        on_initial_rows=lambda snapshot, pending: initial.append(snapshot),
        on_upstream_rows=lambda snapshot, indexes: finalized.append(
            (snapshot, next(iter(indexes)))
        ),
    )

    assert initial[0][0].state is ActionState.MISSING
    assert initial[0][0].upstream is source
    assert finalized[0][0][0].state is ActionState.AVAILABLE
    assert finalized[0][1] == 0
    assert initial[0] is not rows
    assert finalized[0][0] is not rows


def test_cli_streaming_leaves_live_table_as_the_only_final_render(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`list` must not print a duplicate ordinary table after stopping Live."""
    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=True, no_color=True)
    )
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None, on_found=None: (
            on_start and on_start(0),
            [],
        )[-1],
    )
    renders = 0

    def unexpected_render(*args: object, **kwargs: object) -> None:
        nonlocal renders
        renders += 1

    monkeypatch.setattr("maniac.cli.listing._render_list", unexpected_render)

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert renders == 0
    assert "No tools to report." in result.output


def test_cli_streaming_error_keeps_provisional_rows_and_propagates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    renderables: list[object] = []
    provider = _FakeProvider()

    class FakeLive:
        def __init__(self, renderable: object, **kwargs: object) -> None:
            renderables.append(renderable)

        def start(self) -> None:
            return None

        def update(self, renderable: object, *, refresh: bool) -> None:
            renderables.append(renderable)

        def refresh(self) -> None:
            return None

        def stop(self) -> None:
            return None

    def enumerate_installations(on_start=None, on_scan=None, on_found=None):
        assert on_start is not None
        on_start(2)
        return [
            (provider, _installation(binary="alpha")),
            (provider, _installation(binary="alpha-sub", package="alpha")),
        ]

    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=True, no_color=True)
    )
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))
    monkeypatch.setattr("maniac.cli.listing.Live", FakeLive)
    monkeypatch.setattr(
        "maniac.cli.listing._classify",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("local failed")),
    )
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations", enumerate_installations
    )

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    output = io.StringIO()
    Console(file=output, force_terminal=True, no_color=True).print(renderables[-1])
    final = output.getvalue()
    assert "checking…" in final
    assert "alpha-sub" in final
    assert "alpha (2 binaries)" not in final


def test_cli_list_explicit_tools_on_a_terminal_render_a_final_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=True, no_color=True)
    )
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.find_installation",
        lambda name, bin_dir=None: (_FakeProvider(), _installation(binary=name)),
    )

    result = runner.invoke(app, ["list", "gum"])

    assert result.exit_code == 0
    assert "Manpage Reachability" in result.output
    assert "gum" in result.output


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

    assert _classify(provider, inst, "tool", cfg) == (
        ActionState.AVAILABLE,
        PageSource.VENDOR,
    )


def test_classify_missing_when_nothing_resolves_and_install_root_is_empty(
    tmp_path: Path,
) -> None:
    provider = _FakeProvider(local_docs=[])
    inst = _installation()
    cfg = _config(tmp_path)

    assert _classify(provider, inst, "tool", cfg) == (
        ActionState.MISSING,
        PageSource.NONE,
    )


def test_classify_missing_when_no_provider_at_all(tmp_path: Path) -> None:
    cfg = _config(tmp_path)

    assert _classify(None, None, "tool", cfg) == (ActionState.MISSING, PageSource.NONE)


def test_classify_ok_when_man_resolves_and_nothing_suggests_staleness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    installed = tmp_path / "usr" / "man1" / "tool.1"
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )

    assert _classify(None, None, "tool", cfg) == (ActionState.OK, PageSource.SYSTEM)


# -- _classify: Source classification for a reachable page ------------------


def test_classify_source_is_maniac_when_manifest_owns_the_resolved_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    manifest_module.record(
        "tool", installed, Tier.INSTALL_ROOT, "src", "abc123", config=cfg
    )
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )

    assert _classify(None, None, "tool", cfg) == (ActionState.OK, PageSource.MANIAC)


def test_classify_source_is_vendor_when_resolved_page_sits_under_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    root = tmp_path / "install_root"
    installed = root / "share" / "man" / "man1" / "tool.1"
    installed.parent.mkdir(parents=True)
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    inst = _installation(root=root)

    assert _classify(None, inst, "tool", cfg) == (
        ActionState.OK,
        PageSource.VENDOR,
    )


def test_classify_source_is_system_when_resolved_page_is_unowned_and_outside_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    installed = tmp_path / "usr" / "share" / "man" / "man1" / "tool.1.gz"
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    inst = _installation(root=tmp_path / "install_root")

    assert _classify(None, inst, "tool", cfg) == (ActionState.OK, PageSource.SYSTEM)


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
    manifest_module.record(
        "tool", entry_path, Tier.INSTALL_ROOT, "src", "abc123", config=cfg
    )
    installed = entry_dir / "tool.1.gz"  # same base page, compressed
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )

    assert _classify(None, None, "tool", cfg) == (ActionState.OK, PageSource.MANIAC)


def test_classify_managed_page_matched_through_a_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Either side of the comparison may be a symlink; both are resolved first."""
    cfg = _config(tmp_path)
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real_page = real_dir / "tool.1"
    real_page.write_text(".TH TOOL 1\n", encoding="utf-8")
    manifest_module.record(
        "tool", real_page, Tier.INSTALL_ROOT, "src", "abc123", config=cfg
    )

    link_dir = tmp_path / "man" / "man1"
    link_dir.mkdir(parents=True)
    linked_page = link_dir / "tool.1"
    linked_page.symlink_to(real_page)
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: linked_page,
    )

    assert _classify(None, None, "tool", cfg) == (ActionState.OK, PageSource.MANIAC)


# -- _classify: outdated requires positive evidence --------------------------


def test_classify_outdated_when_recorded_version_differs_from_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    manifest_module.record(
        "tool",
        installed,
        Tier.SYNTHESIS,
        "model",
        "abc123",
        config=cfg,
        version="1.0.0",
    )
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    inst = _installation(version="2.0.0")

    assert _classify(None, inst, "tool", cfg) == (
        ActionState.OUTDATED,
        PageSource.MANIAC,
    )


def test_classify_ok_when_entry_records_no_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`local_lib` never reports a version; absent evidence reads `ok`, permanently."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    manifest_module.record(
        "tool", installed, Tier.SYNTHESIS, "model", "abc123", config=cfg, version=None
    )
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    inst = _installation(version="2.0.0")

    assert _classify(None, inst, "tool", cfg) == (ActionState.OK, PageSource.MANIAC)


def test_classify_ok_when_installation_version_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    manifest_module.record(
        "tool",
        installed,
        Tier.SYNTHESIS,
        "model",
        "abc123",
        config=cfg,
        version="1.0.0",
    )
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    inst = _installation(version=None)

    assert _classify(None, inst, "tool", cfg) == (ActionState.OK, PageSource.MANIAC)


def test_classify_outdated_when_unclaimed_binarys_own_version_differs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0020: no `Installation` to compare against, so the binary's own
    `--version` output is asked directly and compared verbatim."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    manifest_module.record(
        "tool",
        installed,
        Tier.SYNTHESIS,
        "model",
        "abc123",
        config=cfg,
        version="1.0.0",
    )
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    monkeypatch.setattr("maniac.cli.listing.get_version", lambda cmd, **kwargs: "2.0.0")

    assert _classify(None, None, "tool", cfg) == (
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
    manifest_module.record(
        "tool",
        installed,
        Tier.SYNTHESIS,
        "model",
        "abc123",
        config=cfg,
        version="1.0.0",
    )
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    monkeypatch.setattr("maniac.cli.listing.get_version", lambda cmd, **kwargs: "1.0.0")

    assert _classify(None, None, "tool", cfg) == (ActionState.OK, PageSource.MANIAC)


def test_classify_ok_when_unclaimed_binarys_version_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`get_version` returning `None` is absence of evidence, not evidence of
    staleness (ADR-0018's positive-evidence rule) -- never `outdated`."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    manifest_module.record(
        "tool",
        installed,
        Tier.SYNTHESIS,
        "model",
        "abc123",
        config=cfg,
        version="1.0.0",
    )
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    monkeypatch.setattr("maniac.cli.listing.get_version", lambda cmd, **kwargs: None)

    assert _classify(None, None, "tool", cfg) == (ActionState.OK, PageSource.MANIAC)


def test_classify_no_subprocess_for_unowned_or_versionless_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The subprocess `get_version` triggers is gated on being owned, having
    a recorded version, and having no `Installation` -- an unowned page and
    an owned-but-versionless one must never reach it, keeping a full `list`
    walk's cost bounded to the handful of rows that qualify."""

    def _unexpected_call(cmd: list[str], **kwargs: object) -> str | None:
        raise AssertionError(f"get_version must not be called for this row: {cmd}")

    monkeypatch.setattr("maniac.cli.listing.get_version", _unexpected_call)
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)

    # Unowned: `man` resolves it, but no manifest entry claims it.
    unowned = cfg.man_dir / "unowned.1"
    unowned.write_text(".TH UNOWNED 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: unowned,
    )
    assert _classify(None, None, "unowned", cfg) == (ActionState.OK, PageSource.SYSTEM)

    # Owned, but the entry itself records no version.
    versionless = cfg.man_dir / "versionless.1"
    versionless.write_text(".TH VERSIONLESS 1\n", encoding="utf-8")
    manifest_module.record(
        "versionless",
        versionless,
        Tier.SYNTHESIS,
        "model",
        "abc123",
        config=cfg,
        version=None,
    )
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: versionless,
    )
    assert _classify(None, None, "versionless", cfg) == (
        ActionState.OK,
        PageSource.MANIAC,
    )


def test_classify_ok_when_page_is_unowned_even_with_a_version_mismatch_in_hand(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`outdated` requires ownership too, not only a version fact somewhere."""
    cfg = _config(tmp_path)
    installed = tmp_path / "usr" / "man1" / "tool.1"
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    inst = _installation(version="2.0.0")

    assert _classify(None, inst, "tool", cfg) == (ActionState.OK, PageSource.SYSTEM)


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
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )

    assert _classify(None, None, "tool", cfg) == (ActionState.OK, PageSource.SYSTEM)


def test_classify_manifest_entry_with_vanished_file_and_no_man_hit_is_missing(
    tmp_path: Path,
) -> None:
    """A manifest entry recorded before a crash between record and copy (ADR-0017)
    is not enough on its own once `man` is also asked."""
    cfg = _config(tmp_path)
    manifest_module.record(
        "tool", cfg.man_dir / "tool.1", Tier.SYNTHESIS, "model", "abc123", config=cfg
    )

    assert _classify(None, None, "tool", cfg) == (ActionState.MISSING, PageSource.NONE)


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
    manifest_module.record(
        "tool", entry_path, Tier.INSTALL_ROOT, "src", "abc123", config=cfg
    )
    # find_installed_manpage_path stays patched to None by the autouse fixture:
    # `man` does not resolve this tool at all, despite the manifest entry.

    assert _classify(None, None, "tool", cfg) == (ActionState.MISSING, PageSource.NONE)

    provider = _FakeProvider(local_docs=[tmp_path / "install_root" / "tool.1"])
    inst = _installation()
    assert _classify(provider, inst, "tool", cfg) == (
        ActionState.AVAILABLE,
        PageSource.VENDOR,
    )


# -- _resolve_upstream: calls provider.resolve_source uniformly (mise's
# registry fallback included) -- ADR-0018 lifted the offline gate, so this
# now proves resolution still works end-to-end and still degrades to a
# blank Upstream rather than crashing `list` when the network call fails.


def test_resolve_upstream_calls_provider_resolve_source() -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    inst = _installation()

    assert _resolve_upstream(provider, inst, config=Config()) is source


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

    source = _resolve_upstream(
        provider, inst, config=Config(config_dir=tmp_path / "config")
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
        _resolve_upstream(
            provider, inst, config=Config(config_dir=tmp_path / "empty-config")
        )
        is None
    )
    discovery._load_mise_registry.cache_clear()


def test_resolve_upstream_none_without_provider_or_installation() -> None:
    assert _resolve_upstream(None, None, config=Config()) is None


# -- _filter_rows: union within an axis, intersection across axes -----------


def _row(
    tool: str, state: ActionState, source: PageSource = PageSource.NONE
) -> ToolRow:
    return ToolRow(
        tool=tool,
        package=tool,
        provider="fake",
        state=state,
        source=source,
        upstream=None,
    )


def test_filter_rows_no_flags_is_unfiltered() -> None:
    rows = [_row("a", ActionState.OK), _row("b", ActionState.MISSING)]

    assert (
        _filter_rows(
            rows, outdated=False, available=False, missing=False, managed=False
        )
        == rows
    )


def test_filter_rows_unions_within_the_state_axis() -> None:
    rows = [
        _row("ok", ActionState.OK),
        _row("avail", ActionState.AVAILABLE),
        _row("miss", ActionState.MISSING),
        _row("stale", ActionState.OUTDATED),
    ]

    filtered = _filter_rows(
        rows, outdated=False, available=True, missing=True, managed=False
    )

    assert [row.tool for row in filtered] == ["avail", "miss"]


def test_filter_rows_intersects_across_axes() -> None:
    rows = [
        _row("stale-managed", ActionState.OUTDATED, PageSource.MANIAC),
        _row("stale-unmanaged", ActionState.OUTDATED, PageSource.SYSTEM),
        _row("ok-managed", ActionState.OK, PageSource.MANIAC),
    ]

    filtered = _filter_rows(
        rows, outdated=True, available=False, missing=False, managed=True
    )

    assert [row.tool for row in filtered] == ["stale-managed"]


# -- CLI: filtering happens before the render branch -------------------------


def test_cli_list_pipe_emits_exactly_the_filtered_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=False, no_color=True)
    )
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))

    available_page = tmp_path / "install_root" / "gum.1"
    available_page.parent.mkdir(parents=True)
    available_page.write_text(".TH GUM 1\n", encoding="utf-8")
    available_provider = _FakeProvider(local_docs=[available_page])
    missing_provider = _FakeProvider(local_docs=[])

    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None, on_found=None: [
            (available_provider, _installation(binary="gum")),
            (missing_provider, _installation(binary="ghost")),
        ],
    )

    res = runner.invoke(app, ["list", "--available"])
    assert res.exit_code == 0
    assert res.output == "gum\n"


def test_cli_list_pipe_available_waits_for_upstream_classification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=False, no_color=True)
    )
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))
    source = RepoSource(name="fzf", target="junegunn/fzf", is_local=False)
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None, on_found=None: [
            (
                _FakeProvider(source=source),
                _installation(binary="fzf", version="0.74.3"),
            ),
            (_FakeProvider(), _installation(binary="missing")),
        ],
    )
    monkeypatch.setattr(
        "maniac.cli.listing.discover_repo_manpage",
        lambda *args, **kwargs: Path("/fzf.1"),
    )

    res = runner.invoke(app, ["list", "--available"])

    assert res.exit_code == 0
    assert res.output == "fzf\n"


# -- _grouped_for_display -----------------------------------------------------


def test_grouped_for_display_solo_tool_keeps_its_own_name() -> None:
    rows = [
        ToolRow(
            tool="pandoc",
            package="pandoc",
            provider="mise",
            state=ActionState.AVAILABLE,
            source=PageSource.VENDOR,
            upstream=None,
        )
    ]

    label, row = _grouped_for_display(rows)[0]
    assert label == "pandoc"
    assert row.state is ActionState.AVAILABLE


def test_grouped_for_display_many_siblings_render_as_package_and_count() -> None:
    """A package exposing many binaries under one state must not blow up the label."""
    rows = [
        ToolRow(
            tool=name,
            package="python",
            provider="mise",
            state=ActionState.OK,
            source=PageSource.MANIAC,
            upstream=None,
        )
        for name in ("python3", "pip", "pydoc", "idle", "2to3")
    ]

    grouped = _grouped_for_display(rows)
    assert len(grouped) == 1
    assert grouped[0][0] == "python (5 binaries)"


def test_grouped_for_display_key_includes_source_and_upstream() -> None:
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

    assert len(_grouped_for_display(rows)) == 2


def test_grouped_for_display_siblings_collapse_despite_differing_reposource_name() -> (
    None
):
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

    grouped = _grouped_for_display(rows)
    assert len(grouped) == 1
    assert grouped[0][0] == "pandoc (3 binaries)"


def test_grouped_for_display_differing_upstream_targets_still_split() -> None:
    """The collapse above must not go too far: a different rendered target
    is a visible difference and still separates rows."""
    rows = [
        ToolRow(
            tool="pandoc",
            package="pandoc",
            provider="mise",
            state=ActionState.AVAILABLE,
            source=PageSource.VENDOR,
            upstream=RepoSource(name="pandoc", target="jgm/pandoc", is_local=False),
        ),
        ToolRow(
            tool="pandoc-lua",
            package="pandoc",
            provider="mise",
            state=ActionState.AVAILABLE,
            source=PageSource.VENDOR,
            upstream=RepoSource(name="pandoc-lua", target="other/fork", is_local=False),
        ),
    ]

    assert len(_grouped_for_display(rows)) == 2


# -- Rendering -----------------------------------------------------------------


def test_render_list_tty_shows_the_four_column_table() -> None:
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, no_color=True)

    _render_list(
        test_console,
        [
            ToolRow(
                tool="pandoc",
                package="pandoc",
                provider="mise",
                state=ActionState.AVAILABLE,
                source=PageSource.VENDOR,
                upstream=None,
            )
        ],
    )

    output = buf.getvalue()
    assert "Manpage Reachability" in output
    assert "pandoc" in output
    assert ActionState.AVAILABLE.value in output
    assert PageSource.VENDOR.value in output


def test_render_list_colors_the_state_column_per_category() -> None:
    buf = io.StringIO()
    test_console = Console(
        file=buf, force_terminal=True, color_system="standard", no_color=False
    )

    _render_list(
        test_console,
        [
            ToolRow(
                tool="pandoc",
                package="pandoc",
                provider="mise",
                state=ActionState.AVAILABLE,
                source=PageSource.VENDOR,
                upstream=None,
            ),
            ToolRow(
                tool="gum",
                package="gum",
                provider="mise",
                state=ActionState.MISSING,
                source=PageSource.NONE,
                upstream=None,
            ),
            ToolRow(
                tool="tmux",
                package="tmux",
                provider="mise",
                state=ActionState.OK,
                source=PageSource.MANIAC,
                upstream=None,
            ),
            ToolRow(
                tool="rg",
                package="rg",
                provider="mise",
                state=ActionState.OUTDATED,
                source=PageSource.MANIAC,
                upstream=None,
            ),
        ],
    )

    output = buf.getvalue()
    assert "\x1b[33mavailable\x1b[0m" in output
    assert "\x1b[31mmissing\x1b[0m" in output
    assert "\x1b[32mok\x1b[0m" in output
    assert "\x1b[33moutdated\x1b[0m" in output


def test_render_list_upstream_column_blank_when_unresolvable() -> None:
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, no_color=True)

    _render_list(
        test_console,
        [
            ToolRow(
                tool="gum",
                package="gum",
                provider="mise",
                state=ActionState.MISSING,
                source=PageSource.NONE,
                upstream=None,
            )
        ],
    )

    assert "Unknown" not in buf.getvalue()


def test_render_list_non_tty_prints_bare_names(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The path `xargs maniac install` relies on."""
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=False)

    _render_list(
        test_console,
        [
            ToolRow(
                tool="gum",
                package="gum",
                provider="mise",
                state=ActionState.MISSING,
                source=PageSource.NONE,
                upstream=None,
            ),
            ToolRow(
                tool="gh",
                package="gh",
                provider="mise",
                state=ActionState.MISSING,
                source=PageSource.NONE,
                upstream=None,
            ),
        ],
    )

    assert capsys.readouterr().out == "gum\ngh\n"
    assert buf.getvalue() == ""


def test_render_list_names_forces_bare_output_on_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True)

    _render_list(
        test_console,
        [
            ToolRow(
                tool="gum",
                package="gum",
                provider="mise",
                state=ActionState.MISSING,
                source=PageSource.NONE,
                upstream=None,
            )
        ],
        names=True,
    )

    assert capsys.readouterr().out == "gum\n"
    assert buf.getvalue() == ""


# -- Progress reporter (unchanged behaviour, ported) -------------------------


def test_progress_reporter_advances_total_and_completed_across_both_phases() -> None:
    """`_ProgressReporter` is `list`'s single combined bar: each phase's
    `on_phase_start` extends one shared total, each `on_scan` advances one
    shared completed count."""
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, no_color=True)
    reporter = _ProgressReporter(test_console)

    reporter.on_phase_start(3)  # discovery phase: 3 $PATH candidates
    reporter.on_scan()
    reporter.on_scan()
    reporter.on_scan()
    reporter.on_phase_start(2)  # row phase: 2 rows found by discovery
    reporter.on_scan()
    reporter.on_scan()

    task = reporter._progress.tasks[0]
    assert task.total == 5
    assert task.completed == 5

    reporter.stop()
    assert reporter._progress.live.is_started is False


# -- CLI smoke tests ----------------------------------------------------------


def test_cli_list_pipe_emits_bare_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Smoke test: `list` through the full CLI, piped, is bare names -- nothing else."""
    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=False, no_color=True)
    )
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: [
            (_FakeProvider(), _installation(binary="gum"))
        ],
    )

    res = runner.invoke(app, ["list"])
    assert res.exit_code == 0
    assert res.output == "gum\n"


def test_cli_list_tty_shows_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=True, no_color=True)
    )
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None, on_found=None: (
            on_start and on_start(1),
            on_found and on_found(_FakeProvider(), _installation(binary="gum")),
            [(_FakeProvider(), _installation(binary="gum"))],
        )[-1],
    )

    res = runner.invoke(app, ["list"])
    assert res.exit_code == 0
    assert "Manpage Reachability" in res.output
