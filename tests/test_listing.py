"""Tests for `list`'s terminal adapter: what is drawn, selected and piped.

Classification facts belong to `tests/test_inventory.py`; everything here is
about the Rich table, the streaming Live frames, the filter flags and the
Typer command wiring.
"""

import io
import threading
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from rich.live_render import LiveRender
from typer.testing import CliRunner

import maniac.cli as cli_module
from maniac.cli import app
from maniac.cli.listing import (
    _SOURCE_COLUMN_MAX_WIDTH,
    _STATE_COLUMN_WIDTH,
    _TOOL_COLUMN_MAX_WIDTH,
    _UPSTREAM_COLUMN_MAX_WIDTH,
    _filter_rows,
    _grouped_for_display,
    _list_table,
    _ProgressReporter,
    _render_list,
    _selected_states,
    _source_cell,
    _streaming_table,
    _StreamingList,
    _TerminalObserver,
)
from maniac.listing import ActionState, PageSource, ToolRow, compute_rows
from maniac.models import RepoSource
from maniac.sources.packages import ExternalPageFreshness, ExternalPageVerification

from .listing_support import _config, _FakeProvider, _installation

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_real_man(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the rendering and CLI paths off the machine's real `man` database."""
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: None,
    )


def test_streaming_list_renders_checking_before_a_blocked_probe_finishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The first Live frame is local truth, never a premature remote miss."""
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    inst = _installation()
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None: [(provider, inst)],
    )
    probe_started = threading.Event()
    release_probe = threading.Event()

    def blocked_probe(*args: object, **kwargs: object) -> tuple[Path, bool]:
        probe_started.set()
        assert release_probe.wait(timeout=2)
        return Path("/page"), True

    monkeypatch.setattr("maniac.listing.upstream.discover_repo_manpage", blocked_probe)

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
                observer=_TerminalObserver(None, renderer),
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


def test_streaming_list_idle_flushes_a_quiet_dirty_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates = 0

    class FakeLive:
        def __init__(self, renderable: object, **kwargs: object) -> None:
            return None

        def start(self) -> None:
            return None

        def update(self, renderable: object, *, refresh: bool) -> None:
            nonlocal updates
            assert not refresh
            updates += 1

        def refresh(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class FakeReporter:
        def stop(self) -> None:
            return None

    clock = iter((0.0, 0.01, 0.25))
    monkeypatch.setattr("maniac.cli.listing.Live", FakeLive)
    monkeypatch.setattr("maniac.cli.listing.monotonic", lambda: next(clock))
    rows = [ToolRow("tool", "tool", "fake", ActionState.MISSING, PageSource.NONE, None)]
    renderer = _StreamingList(Console(file=io.StringIO()), FakeReporter())

    renderer.skeleton(rows)
    renderer.local(rows, 0, True)
    renderer.idle()

    assert updates == 1


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

    console = Console(
        file=io.StringIO(), force_terminal=True, no_color=True, width=80, height=6
    )
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

    table = _streaming_table(
        rows, set(range(len(rows))), terminal_width=80, maximum_rows=10
    )

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
        kwargs["observer"].inventory_ready(tuple(rows))
        for index in range(len(rows)):
            kwargs["observer"].row_classified(tuple(rows), index, False)
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
        kwargs["observer"].inventory_ready(tuple(rows))
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
    checking = _streaming_table(
        rows, {0, 1}, terminal_width=120, upstream_width=_UPSTREAM_COLUMN_MAX_WIDTH
    )
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
    resolved = _streaming_table(
        resolved_rows,
        {1},
        terminal_width=120,
        upstream_width=_UPSTREAM_COLUMN_MAX_WIDTH,
    )

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
    assert checking_columns[1].width == _STATE_COLUMN_WIDTH
    assert checking_columns[2].width == _SOURCE_COLUMN_MAX_WIDTH
    assert checking_columns[3].width == _UPSTREAM_COLUMN_MAX_WIDTH
    assert not checking.expand

    output = io.StringIO()
    Console(file=output, force_terminal=True, no_color=True, width=60).print(resolved)
    assert "…" in output.getvalue()


def test_streaming_table_renders_unverified_without_truncation() -> None:
    rows = [
        ToolRow(
            "tool",
            "tool",
            "fake",
            ActionState.UNVERIFIED,
            PageSource.SYSTEM,
            None,
        )
    ]

    table = _streaming_table(rows, set(), terminal_width=80)
    output = io.StringIO()
    Console(file=output, force_terminal=True, no_color=True, width=80).print(table)

    assert table.columns[1].width == _STATE_COLUMN_WIDTH
    assert "unverified" in output.getvalue()
    assert "unverifi…" not in output.getvalue()


def test_list_tables_cap_upstream_width_and_keep_ellipsis_stable() -> None:
    row = ToolRow(
        "tool",
        "tool",
        "fake",
        ActionState.AVAILABLE,
        PageSource.UPSTREAM,
        RepoSource(
            name="tool",
            target="owner/" + "very-long-upstream-name-" * 8,
            is_local=False,
        ),
    )
    tables = [
        _streaming_table([row], set(), terminal_width=120),
        _list_table([row], terminal_width=120),
    ]

    for table in tables:
        assert not table.expand
        assert table.columns[3].width == _UPSTREAM_COLUMN_MAX_WIDTH

    renders = []
    for width in (120, 160):
        output = io.StringIO()
        Console(
            file=output,
            force_terminal=True,
            legacy_windows=True,
            no_color=True,
            width=width,
        ).print(tables[0])
        renders.append(output.getvalue())

    assert all(
        "owner/very-long-upstream-name-very-long-upstrea…" in render
        for render in renders
    )
    assert all(
        len(next(line for line in render.splitlines() if line.startswith("┌"))) < 120
        for render in renders
    )


def test_list_table_sizes_upstream_to_its_widest_value() -> None:
    """The reported defect: a 37-character identity was cut to 24."""
    widest = "redhat-developer/yaml-language-server"
    rows = [
        ToolRow(
            "yaml-language-server",
            "yaml-language-server",
            "fake",
            ActionState.MISSING,
            PageSource.NONE,
            RepoSource(name="yaml-language-server", target=widest, is_local=False),
        ),
        ToolRow(
            "rg",
            "rg",
            "fake",
            ActionState.MISSING,
            PageSource.NONE,
            RepoSource(name="rg", target="BurntSushi/ripgrep", is_local=False),
        ),
    ]

    table = _list_table(rows, terminal_width=160)

    assert table.columns[3].width == len(widest)
    assert not table.expand

    output = io.StringIO()
    Console(file=output, force_terminal=True, no_color=True, width=160).print(table)
    assert widest in output.getvalue()
    assert "…" not in output.getvalue()


def test_list_table_never_narrows_upstream_below_its_header() -> None:
    rows = [ToolRow("tool", "tool", "fake", ActionState.MISSING, PageSource.NONE, None)]

    assert _list_table(rows, terminal_width=160).columns[3].width == len("Upstream")


def test_list_table_renders_a_local_upstream_as_a_linked_path() -> None:
    path = Path.home() / "Projects" / "claude2agents"
    rows = [
        ToolRow(
            "claude2agents",
            "claude2agents",
            "fake",
            ActionState.MISSING,
            PageSource.NONE,
            RepoSource(
                name="claude2agents",
                target=f"LOCAL:{path}",
                is_local=True,
                local_path=path,
            ),
        )
    ]

    table = _list_table(rows, terminal_width=160)
    output = io.StringIO()
    Console(file=output, force_terminal=True, no_color=True, width=160).print(table)
    rendered = output.getvalue()

    assert "LOCAL:" not in rendered
    assert "~/Projects/claude2agents" in rendered
    assert path.as_uri() in rendered
    assert table.columns[3].width == len("~/Projects/claude2agents")


def test_streaming_upstream_width_does_not_change_as_rows_arrive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0024: the live table's geometry is fixed before any identity exists."""
    tables: list[Any] = []

    class FakeLive:
        def __init__(self, renderable: Any, **kwargs: object) -> None:
            tables.append(renderable)

        def start(self) -> None:
            return None

        def update(self, renderable: Any, *, refresh: bool) -> None:
            tables.append(renderable)

        def refresh(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class FakeReporter:
        def stop(self) -> None:
            return None

    monkeypatch.setattr("maniac.cli.listing.Live", FakeLive)
    renderer = _StreamingList(
        Console(file=io.StringIO(), width=160, height=40), FakeReporter()
    )
    unresolved = [
        ToolRow(name, name, "fake", ActionState.MISSING, PageSource.NONE, None)
        for name in ("alpha", "beta")
    ]
    resolved = [
        ToolRow(
            "alpha",
            "alpha",
            "fake",
            ActionState.MISSING,
            PageSource.NONE,
            RepoSource(
                name="alpha",
                target="redhat-developer/yaml-language-server",
                is_local=False,
            ),
        ),
        unresolved[1],
    ]

    renderer.skeleton(unresolved)
    renderer.upstream(resolved, {0})
    renderer.stop()

    assert len(tables) > 1
    widths = {table.columns[3].width for table in tables}
    assert widths == {_UPSTREAM_COLUMN_MAX_WIDTH}


def test_streaming_list_never_collapses_siblings_while_results_land(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0024: siblings stay one row each for the live table's whole life."""
    tables: list[Any] = []

    class FakeLive:
        transient = False

        def __init__(self, renderable: Any, **kwargs: object) -> None:
            tables.append(renderable)

        def start(self) -> None:
            return None

        def update(self, renderable: Any, *, refresh: bool) -> None:
            tables.append(renderable)

        def refresh(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class FakeReporter:
        def stop(self) -> None:
            return None

    monkeypatch.setattr("maniac.cli.listing.Live", FakeLive)
    renderer = _StreamingList(
        Console(file=io.StringIO(), width=160, height=40), FakeReporter()
    )
    siblings = ("pandoc", "pandoc-lua", "pandoc-server")
    unresolved = [
        ToolRow(binary, "pandoc", "fake", ActionState.MISSING, PageSource.NONE, None)
        for binary in siblings
    ]
    resolved = [
        ToolRow(
            binary,
            "pandoc",
            "fake",
            ActionState.AVAILABLE,
            PageSource.UPSTREAM,
            RepoSource(name=binary, target="jgm/pandoc", is_local=False),
        )
        for binary in siblings
    ]

    renderer.skeleton(unresolved)
    renderer.upstream(resolved, {0, 1, 2})
    renderer.stop()

    assert len(tables) > 1
    for table in tables:
        assert [str(cell) for cell in table.columns[0]._cells] == list(siblings)


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


def test_cli_streaming_discards_the_live_frames_for_one_final_render(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The grouped final render replaces the live table; it never follows it."""
    transients: list[bool] = []

    class FakeLive:
        def __init__(self, renderable: object, **kwargs: object) -> None:
            self.transient = False

        def start(self) -> None:
            return None

        def update(self, renderable: object, *, refresh: bool) -> None:
            return None

        def refresh(self) -> None:
            return None

        def stop(self) -> None:
            transients.append(self.transient)

    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=True, no_color=True)
    )
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))
    monkeypatch.setattr("maniac.cli.listing.Live", FakeLive)
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None: (
            on_start and on_start(1),
            [(_FakeProvider(), _installation(binary="gum"))],
        )[-1],
    )
    renders: list[Any] = []

    def record_render(*args: Any, **kwargs: Any) -> None:
        renders.append(args[1])

    monkeypatch.setattr("maniac.cli.listing._render_list", record_render)

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    # The short table is on the normal screen, so Rich must be told to erase it.
    assert transients == [True]
    assert [row.tool for row in renders[0]] == ["gum"]


def test_cli_streaming_keeps_siblings_apart_absent_proven_shared_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sharing a package is not enough to collapse (ADR-0049): three distinct,
    unreadable binaries under one package still render as three rows."""
    output = io.StringIO()
    monkeypatch.setattr(
        cli_module.console,
        "_instance",
        Console(file=output, force_terminal=True, no_color=True, width=120),
    )
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))
    provider = _FakeProvider()
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None: [
            (provider, _installation(binary=binary, package="pandoc"))
            for binary in ("pandoc", "pandoc-lua", "pandoc-server")
        ],
    )

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    final = output.getvalue()
    # Live frames are transient, so only the grouped table survives the run.
    table = final[final.rindex("Manpage Reachability") :]
    assert "pandoc" in table
    assert "pandoc-lua" in table
    assert "pandoc-server" in table
    assert "pandoc (3 binaries)" not in table


def test_cli_verbose_list_disables_streaming(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = io.StringIO()
    monkeypatch.setattr(
        cli_module.console,
        "_instance",
        Console(file=output, force_terminal=True, no_color=True, width=120),
    )
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        "maniac.cli.listing.Live",
        lambda *args, **kwargs: pytest.fail("verbose list must not start Live"),
    )
    provider = _FakeProvider()
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None: [(provider, _installation(binary="gum"))],
    )

    result = runner.invoke(app, ["--verbose", "list"])

    assert result.exit_code == 0
    assert "Manpage Reachability" in output.getvalue()


def test_cli_streaming_stops_row_progress_after_the_skeleton(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The live table, not an invisible progress task, owns row-phase feedback."""
    captured: dict[str, object] = {}

    class FakeReporter:
        def __init__(self, target_console: Console) -> None:
            self.phases: list[int] = []
            self.scans = 0

        def on_phase_start(self, total: int) -> None:
            self.phases.append(total)

        def on_scan(self) -> None:
            self.scans += 1

        def stop(self) -> None:
            return None

    def rows(*args: object, **kwargs: Any) -> list[ToolRow]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=True, no_color=True)
    )
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))
    monkeypatch.setattr("maniac.cli.listing._ProgressReporter", FakeReporter)
    monkeypatch.setattr("maniac.cli.listing.compute_rows", rows)

    result = runner.invoke(app, ["list"])

    observer = captured["observer"]
    assert isinstance(observer, _TerminalObserver)
    reporter = observer._reporter
    assert isinstance(reporter, FakeReporter)
    observer.discovery_started(7)
    observer.discovery_scanned()
    observer.rows_started(7)
    observer.row_scanned()

    assert result.exit_code == 0
    # Discovery still reports; the row phase stops at the skeleton.
    assert reporter.phases == [7]
    assert reporter.scans == 1


def test_cli_blocking_terminal_list_keeps_combined_row_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Explicit tool selection remains blocking and retains its combined bar."""
    captured: dict[str, object] = {}

    class FakeReporter:
        def __init__(self, target_console: Console) -> None:
            self.phases: list[int] = []
            self.scans = 0

        def on_phase_start(self, total: int) -> None:
            self.phases.append(total)

        def on_scan(self) -> None:
            self.scans += 1

        def stop(self) -> None:
            return None

    def rows(*args: object, **kwargs: Any) -> list[ToolRow]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=True, no_color=True)
    )
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))
    monkeypatch.setattr("maniac.cli.listing._ProgressReporter", FakeReporter)
    monkeypatch.setattr("maniac.cli.listing.compute_rows", rows)

    result = runner.invoke(app, ["list", "gum"])

    observer = captured["observer"]
    assert isinstance(observer, _TerminalObserver)
    reporter = observer._reporter
    assert isinstance(reporter, FakeReporter)
    observer.rows_started(3)
    observer.row_scanned()

    assert result.exit_code == 0
    assert reporter.phases == [3]
    assert reporter.scans == 1


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

    def enumerate_installations(on_start=None, on_scan=None):
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
        "maniac.listing.inventory.classify",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("local failed")),
    )
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        enumerate_installations,
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
        "maniac.listing.inventory.resolution.find_installation",
        lambda name, bin_dir=None: (_FakeProvider(), _installation(binary=name)),
    )

    result = runner.invoke(app, ["list", "gum"])

    assert result.exit_code == 0
    assert "Manpage Reachability" in result.output
    assert "gum" in result.output


# -- _filter_rows: union within an axis, intersection across axes -----------


def _row(
    tool: str,
    state: ActionState,
    source: PageSource = PageSource.NONE,
    *,
    managed: bool = False,
) -> ToolRow:
    return ToolRow(
        tool=tool,
        package=tool,
        provider="fake",
        state=state,
        source=source,
        upstream=None,
        managed=managed,
    )


def test_filter_rows_no_flags_is_unfiltered() -> None:
    rows = [_row("a", ActionState.OK), _row("b", ActionState.MISSING)]

    assert _filter_rows(rows, states=frozenset(), managed=False) == rows


def test_filter_rows_unions_within_the_state_axis() -> None:
    rows = [
        _row("ok", ActionState.OK),
        _row("avail", ActionState.AVAILABLE),
        _row("miss", ActionState.MISSING),
        _row("stale", ActionState.OUTDATED),
    ]

    filtered = _filter_rows(
        rows,
        states=_selected_states(
            outdated=False,
            unverified=False,
            misattributed=False,
            available=True,
            missing=True,
        ),
        managed=False,
    )

    assert [row.tool for row in filtered] == ["avail", "miss"]


def test_filter_rows_selects_unverified_rows() -> None:
    rows = [
        _row("unproven", ActionState.UNVERIFIED, PageSource.SYSTEM),
        _row("current", ActionState.OK, PageSource.SYSTEM),
    ]

    filtered = _filter_rows(
        rows,
        states=_selected_states(
            outdated=False,
            unverified=True,
            misattributed=False,
            available=False,
            missing=False,
        ),
        managed=False,
    )

    assert [row.tool for row in filtered] == ["unproven"]


def test_filter_rows_selects_misattributed_rows() -> None:
    rows = [
        _row("wrong-owner", ActionState.MISATTRIBUTED, PageSource.SYSTEM),
        _row("current", ActionState.OK, PageSource.SYSTEM),
    ]

    filtered = _filter_rows(
        rows,
        states=_selected_states(
            outdated=False,
            unverified=False,
            misattributed=True,
            available=False,
            missing=False,
        ),
        managed=False,
    )

    assert [row.tool for row in filtered] == ["wrong-owner"]


def test_filter_rows_intersects_across_axes() -> None:
    rows = [
        _row("stale-managed", ActionState.OUTDATED, PageSource.UPSTREAM, managed=True),
        _row("stale-unmanaged", ActionState.OUTDATED, PageSource.SYSTEM),
        _row("ok-managed", ActionState.OK, PageSource.VENDOR, managed=True),
    ]

    filtered = _filter_rows(
        rows,
        states=_selected_states(
            outdated=True,
            unverified=False,
            misattributed=False,
            available=False,
            missing=False,
        ),
        managed=True,
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
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None: [
            (available_provider, _installation(binary="gum")),
            (missing_provider, _installation(binary="ghost")),
        ],
    )

    res = runner.invoke(app, ["list", "--available"])
    assert res.exit_code == 0
    assert res.output == "gum\n"


def test_cli_list_pipe_unverified_emits_exactly_the_filtered_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=False, no_color=True)
    )
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))
    page = tmp_path / "usr" / "share" / "man" / "man1" / "tool.1"
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: page,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.verify_external_page",
        lambda page, **kwargs: ExternalPageVerification(
            ExternalPageFreshness.UNVERIFIED, None
        ),
    )
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None: [(_FakeProvider(), _installation())],
    )

    res = runner.invoke(app, ["list", "--unverified"])

    assert res.exit_code == 0
    assert res.output == "tool\n"


def test_cli_list_pipe_misattributed_emits_exactly_the_filtered_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=False, no_color=True)
    )
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))
    page = tmp_path / "usr" / "share" / "man" / "man1" / "python.1"
    monkeypatch.setattr(
        "maniac.listing.classification.find_installed_manpage_path",
        lambda man_bin, tool_name: page,
    )
    monkeypatch.setattr(
        "maniac.listing.classification.verify_external_page",
        lambda page, **kwargs: ExternalPageVerification(
            ExternalPageFreshness.WRONG_OWNER, "python3.12-minimal"
        ),
    )
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None: [
            (_FakeProvider(), _installation(binary="python"))
        ],
    )

    res = runner.invoke(app, ["list", "--misattributed"])

    assert res.exit_code == 0
    assert res.output == "python\n"


def test_cli_list_pipe_available_waits_for_upstream_classification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=False, no_color=True)
    )
    monkeypatch.setattr(cli_module, "Config", lambda: _config(tmp_path))
    source = RepoSource(name="fzf", target="junegunn/fzf", is_local=False)
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None: [
            (
                _FakeProvider(source=source),
                _installation(binary="fzf", version="0.74.3"),
            ),
            (_FakeProvider(), _installation(binary="missing")),
        ],
    )
    monkeypatch.setattr(
        "maniac.listing.upstream.discover_repo_manpage",
        lambda *args, **kwargs: (Path("/fzf.1"), True),
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
    assert grouped[0][0] == "pip (5 binaries)"


def test_grouped_for_display_label_tie_breaks_alphabetically() -> None:
    """Two members tied on name length anchor the label on the alphabetically first."""
    rows = [
        ToolRow(
            tool=name,
            package="tool",
            provider="mise",
            state=ActionState.OK,
            source=PageSource.MANIAC,
            upstream=None,
        )
        for name in ("zeta", "alfa")
    ]

    grouped = _grouped_for_display(rows)
    assert len(grouped) == 1
    assert grouped[0][0] == "alfa (2 binaries)"


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


def test_grouped_for_display_differing_page_path_still_splits(tmp_path: Path) -> None:
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

    assert len(_grouped_for_display(rows)) == 2


def test_grouped_for_display_differing_owning_package_still_splits() -> None:
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

    assert len(_grouped_for_display(rows)) == 2


def test_grouped_for_display_differing_target_cluster_still_splits() -> None:
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

    assert len(_grouped_for_display(rows)) == 2


def test_grouped_for_display_sorts_by_rendered_label_not_first_encounter() -> None:
    """`docs/BACKLOG.md`'s sort-order defect: a group must land where its own
    label sorts, not wherever its earliest-alphabetical member happened to be
    discovered. `zoxide` is discovered before `alpha`, but must render after it."""
    rows = [
        ToolRow(
            tool="zoxide",
            package="zoxide",
            provider="mise",
            state=ActionState.OK,
            source=PageSource.VENDOR,
            upstream=None,
        ),
        ToolRow(
            tool="alpha",
            package="alpha",
            provider="mise",
            state=ActionState.OK,
            source=PageSource.VENDOR,
            upstream=None,
        ),
    ]

    grouped = _grouped_for_display(rows)

    assert [label for label, _ in grouped] == ["alpha", "zoxide"]


# -- Rendering -----------------------------------------------------------------


def test_vendor_source_keyword_links_to_the_local_manpage(tmp_path: Path) -> None:
    page = tmp_path / "tool.1"
    row = ToolRow(
        "tool",
        "tool",
        "fake",
        ActionState.AVAILABLE,
        PageSource.VENDOR,
        None,
        page_path=page,
    )
    output = io.StringIO()
    Console(file=output, force_terminal=True, color_system="standard").print(
        _source_cell(row)
    )

    assert page.absolute().as_uri() in output.getvalue()


def test_upstream_source_keyword_links_to_the_upstream_manpage() -> None:
    uri = "https://github.com/owner/tool/blob/v1.2.3/man/tool.1"
    row = ToolRow(
        "tool",
        "tool",
        "fake",
        ActionState.AVAILABLE,
        PageSource.UPSTREAM,
        None,
        page_path=Path("/cached/tool.1"),
        page_uri=uri,
    )
    output = io.StringIO()
    Console(file=output, force_terminal=True, color_system="standard").print(
        _source_cell(row)
    )

    assert uri in output.getvalue()
    assert "file:///cached/tool.1" not in output.getvalue()


def test_system_source_shows_owning_package_when_provable() -> None:
    row = ToolRow(
        "python",
        "python",
        "mise",
        ActionState.UNVERIFIED,
        PageSource.SYSTEM,
        None,
        owning_package="python3.12-minimal",
    )
    output = io.StringIO()
    Console(file=output, force_terminal=True, color_system="standard").print(
        _source_cell(row)
    )

    assert "python3.12-minimal" in output.getvalue()
    assert "system" not in output.getvalue()


def test_system_source_without_provable_owner_still_shows_plain_system() -> None:
    row = ToolRow(
        "tool", "tool", "fake", ActionState.UNVERIFIED, PageSource.SYSTEM, None
    )
    output = io.StringIO()
    Console(file=output, force_terminal=True, color_system="standard").print(
        _source_cell(row)
    )

    assert output.getvalue().strip() == "system"


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
                tool="unproven",
                package="unproven",
                provider="mise",
                state=ActionState.UNVERIFIED,
                source=PageSource.SYSTEM,
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
    assert "\x1b[33munverified\x1b[0m" in output
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


def test_render_list_marks_drift_next_to_the_tool_name() -> None:
    """A manifest entry whose manpath link the scan found broken gets a
    marker on Tool -- no new column (docs/BACKLOG.md's width constraint)."""
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, no_color=True)

    _render_list(
        test_console,
        [
            ToolRow(
                tool="drifted",
                package="drifted",
                provider="mise",
                state=ActionState.MISSING,
                source=PageSource.NONE,
                upstream=None,
                drift=True,
            ),
            ToolRow(
                tool="sound",
                package="sound",
                provider="mise",
                state=ActionState.OK,
                source=PageSource.MANIAC,
                upstream=None,
                drift=False,
            ),
        ],
    )

    output = buf.getvalue()
    drifted_line = next(line for line in output.splitlines() if "drifted" in line)
    sound_line = next(line for line in output.splitlines() if "sound" in line)
    assert "⚠" in drifted_line
    assert "⚠" not in sound_line


def test_render_list_drift_marker_survives_ellipsis_on_a_capped_label() -> None:
    """The Tool column ellipsizes from the right at its cap -- a marker
    appended there would be the first thing cut, hiding drift silently."""
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, no_color=True)
    long_name = "a" * (_TOOL_COLUMN_MAX_WIDTH + 5)

    _render_list(
        test_console,
        [
            ToolRow(
                tool=long_name,
                package=long_name,
                provider="mise",
                state=ActionState.MISSING,
                source=PageSource.NONE,
                upstream=None,
                drift=True,
            )
        ],
    )

    drifted_line = next(
        line for line in buf.getvalue().splitlines() if "a" * 10 in line
    )
    assert "⚠" in drifted_line


def test_render_list_drift_marker_survives_grouping_by_any_sibling() -> None:
    """Drift is per-tool manifest evidence, outside the grouping key -- a
    group must not hide one drifted sibling behind its representative."""
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, no_color=True)

    _render_list(
        test_console,
        [
            ToolRow(
                tool="alpha",
                package="shared",
                provider="mise",
                state=ActionState.OK,
                source=PageSource.MANIAC,
                upstream=None,
                drift=True,
            ),
            ToolRow(
                tool="beta",
                package="shared",
                provider="mise",
                state=ActionState.OK,
                source=PageSource.MANIAC,
                upstream=None,
                drift=False,
            ),
        ],
    )

    assert "⚠" in buf.getvalue()


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


def test_render_list_non_tty_prints_bare_names_even_when_drifted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The piped path stays bare names regardless of drift (settled exclusion,
    `docs/BACKLOG.md`): the drift marker is a terminal-table affordance only."""
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
                drift=True,
            )
        ],
    )

    assert capsys.readouterr().out == "gum\n"
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
        "maniac.listing.inventory.resolution.enumerate_installations",
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
        "maniac.listing.inventory.resolution.enumerate_installations",
        lambda on_start=None, on_scan=None: (
            on_start and on_start(1),
            [(_FakeProvider(), _installation(binary="gum"))],
        )[-1],
    )

    res = runner.invoke(app, ["list"])
    assert res.exit_code == 0
    assert "Manpage Reachability" in res.output
