"""`list`: the terminal face of `maniac.listing`, and nothing else.

Every reachability fact comes from the inventory service; this module only
selects, groups and draws. `--unverified`/`--outdated`/`--available`/`--missing`
filters the State axis and `--managed` filters manifest ownership independently
of page provenance. Filters union within an axis and intersect across axes; no
flags means no filtering. There is no `--ok`, deliberately (ADR-0018): it would
select exactly the rows needing no action.
"""

from time import monotonic
from typing import Annotated, Any

import typer
from rich.live import Live
from rich.progress import Progress
from rich.table import Table

from ..listing import (
    ActionState,
    InventoryObserver,
    PageSource,
    RowSnapshot,
    ToolRow,
    compute_rows,
)
from ..models import RepoSource
from . import app, console, get_config
from .render import _repo_cell

# Traffic-light by what remains to be done: green needs nothing, yellow
# needs a free reinstall or install, red needs an LLM.
_STATE_COLOR: dict[ActionState, str] = {
    ActionState.OK: "green",
    ActionState.UNVERIFIED: "yellow",
    ActionState.OUTDATED: "yellow",
    ActionState.AVAILABLE: "yellow",
    ActionState.MISSING: "red",
}

_STATE_COLUMN_WIDTH = max(
    len("checking…"), *(len(state.value) for state in ActionState)
)
_TOOL_COLUMN_MAX_WIDTH = 24
# A provable owning package can render in place of "system" (`_source_cell`),
# and Debian package names run long -- "python3.12-minimal" is 19 already.
# Capped the same way Tool is, so the longest name on a real system cannot
# blow out the table.
_SOURCE_COLUMN_MAX_WIDTH = 24
# Wide enough for the longest realistic repository identity -- GitHub caps an
# owner at 39 characters and the widest "owner/repo" on a live inventory here
# is 37 -- and for a home-relative checkout path. Still a cap, so the column
# never grows with the terminal.
_UPSTREAM_COLUMN_MAX_WIDTH = 48
# Four columns of single-space padding on each side, plus five box rules.
_TABLE_CHROME_WIDTH = 13


class _ProgressReporter:
    """Progress bar for blocking interactive `list` calls.

    Discovery and per-row reachability report through `on_phase_start` and
    `on_scan`, sharing one task and combined total. Streaming calls stop it
    once the table skeleton is ready and do not send later row callbacks.
    `transient=True` clears it before a final table prints.
    """

    def __init__(self, target_console: Any) -> None:
        self._total = 0
        self._done = 0
        # `Live.start` does `with self.console:` -- a dunder lookup that
        # bypasses `cli._LazyConsole.__getattr__` entirely, so `Progress`
        # needs the real `rich.console.Console` underneath it, not the lazy
        # wrapper. Any forwarded attribute access forces it into existence.
        _ = target_console.is_terminal
        resolved_console = getattr(target_console, "_instance", target_console)
        self._progress = Progress(console=resolved_console, transient=True)
        self._progress.start()
        self._task_id = self._progress.add_task("Scanning...", total=None)

    def on_phase_start(self, total: int) -> None:
        self._total += total
        self._progress.update(self._task_id, total=self._total)

    def on_scan(self) -> None:
        self._done += 1
        self._progress.update(self._task_id, completed=self._done)

    def stop(self) -> None:
        self._progress.stop()


def _bare_names(rows: list[ToolRow]) -> list[str]:
    """Deduplicated binary names in first-seen order, for the pipe-friendly path."""
    seen: dict[str, None] = {}
    for row in rows:
        seen.setdefault(row.tool, None)
    return list(seen)


def _upstream_key(upstream: RepoSource | None) -> str | None:
    """Hashable identity of a `RepoSource` by what the Upstream column renders.

    `RepoSource.name` is deliberately excluded: it carries the *binary*
    name, which is never displayed, so including it split groups that
    render identically. `pandoc`, `pandoc-lua` and `pandoc-server` all
    resolve to `jgm/pandoc` and showed as three separate rows purely
    because their `name` fields differed. Two rows may collapse only when
    every cell a reader can see agrees, so the key is exactly the rendered
    cell and nothing behind it.
    """
    if upstream is None:
        return None
    return upstream.identity


def _grouped_for_display(rows: list[ToolRow]) -> list[tuple[str, ToolRow]]:
    """Collapse binaries sharing one (provider, package, state, source, upstream) into one row.

    A solo group's label is its one tool's name, unchanged. A group of
    several is the package name with a count suffix, e.g. `python (12
    binaries)`, not every sibling's name comma-joined: that joined form is
    unbounded -- a package can expose a dozen-plus binaries under one state
    -- and blows up the Tool column's width, breaking the reader's ability
    to track rows by eye down the table.

    Package identity is a display grouping only -- `pandoc`, `pandoc-lua`
    and `pandoc-server` share one install root but are three separate `man`
    lookups. Source and upstream join provider/package/state in the key so
    two rows differing in either can never collapse into one that hides the
    difference; the returned representative row's other fields (state,
    source, upstream) are shared across the whole group by construction.

    Known defect, left exactly as ADR-0018 found it: a solo group is
    labelled by tool name and a multi-binary group by package name, so one
    package split across two states can still render two rows a reader
    cannot tell apart by label alone (`docs/BACKLOG.md`).
    """
    groups: dict[tuple[str, str, ActionState, PageSource, Any], list[ToolRow]] = {}
    order: list[tuple[str, str, ActionState, PageSource, Any]] = []
    for row in rows:
        key = (
            row.provider,
            row.package,
            row.state,
            row.source,
            _upstream_key(row.upstream),
        )
        if key not in groups:
            order.append(key)
        groups.setdefault(key, []).append(row)

    rendered: list[tuple[str, ToolRow]] = []
    for key in order:
        group = groups[key]
        representative = group[0]
        label = (
            representative.tool
            if len(group) == 1
            else f"{representative.package} ({len(group)} binaries)"
        )
        rendered.append((label, representative))
    return rendered


def _upstream_cell(upstream: RepoSource | None) -> Any:
    """Render the Upstream column: blank when nothing was resolvable (ADR-0018)."""
    from rich.text import Text

    if upstream is None:
        return Text("")
    return _repo_cell(upstream, blank_when_unresolvable=True)


def _tool_column_width(labels: list[str]) -> int:
    """Widest Tool label, capped, and never below the header."""
    return min(
        _TOOL_COLUMN_MAX_WIDTH, max([len("Tool"), *(len(label) for label in labels)])
    )


def _source_label(row: ToolRow) -> str:
    """The text `_source_cell` renders: a provable owning package, else the source."""
    if row.source is PageSource.SYSTEM and row.owning_package is not None:
        return row.owning_package
    return row.source.value


def _source_column_width(labels: list[str]) -> int:
    """Widest Source label, capped, and never below the header."""
    return min(
        _SOURCE_COLUMN_MAX_WIDTH,
        max([len("Source"), *(len(label) for label in labels)]),
    )


def _upstream_budget(
    tool_labels: list[str], source_width: int, terminal_width: int
) -> int:
    """Columns Upstream may take: the cap, less whatever the terminal cannot spare.

    Upstream yields first because it is the one column whose ellipsis is
    expected; letting the cap push the table past the terminal makes Rich
    shrink State instead, truncating `unverified` and `checking…`.
    """
    fixed = (
        _tool_column_width(tool_labels)
        + _STATE_COLUMN_WIDTH
        + source_width
        + _TABLE_CHROME_WIDTH
    )
    return max(len("Upstream"), min(_UPSTREAM_COLUMN_MAX_WIDTH, terminal_width - fixed))


def _upstream_width(cells: list[Any], budget: int) -> int:
    """Widest rendered Upstream cell within `budget`, never below the header."""
    return min(budget, max([len("Upstream"), *(len(cell.plain) for cell in cells)]))


def _source_cell(row: ToolRow) -> Any:
    """Render page provenance, linked to the selected or reachable page."""
    from rich.style import Style
    from rich.text import Text

    text = Text(_source_label(row))
    link = row.page_uri
    if (
        link is None
        and row.page_path is not None
        and row.source is not PageSource.UPSTREAM
    ):
        link = row.page_path.absolute().as_uri()
    if link is not None and row.source is not PageSource.NONE:
        text.stylize(
            Style(link=link),
            0,
            len(text),
        )
    return text


def _selected_states(
    *, outdated: bool, unverified: bool, available: bool, missing: bool
) -> frozenset[ActionState]:
    """The State axis the flags select; empty means the axis is unconstrained."""
    return frozenset(
        state
        for state, flag in (
            (ActionState.OUTDATED, outdated),
            (ActionState.UNVERIFIED, unverified),
            (ActionState.AVAILABLE, available),
            (ActionState.MISSING, missing),
        )
        if flag
    )


def _filter_rows(
    rows: list[ToolRow], *, states: frozenset[ActionState], managed: bool
) -> list[ToolRow]:
    """Narrow `rows` by State and the independent MANIAC ownership filter.

    No flag set at all means no filtering. `--available --missing` unions
    within the State axis to every row worth acting on; `--managed
    --outdated` intersects across axes to MANIAC's own stale pages.
    """
    if not states and not managed:
        return rows
    return [
        row
        for row in rows
        if (not states or row.state in states) and (not managed or row.managed)
    ]


def _list_table(rows: list[ToolRow], *, terminal_width: int) -> Table:
    """Build the ordinary, completed list table."""
    rendered = [
        (label, row, _upstream_cell(row.upstream))
        for label, row in _grouped_for_display(rows)
    ]
    source_width = _source_column_width([_source_label(row) for _, row, _ in rendered])
    table = Table(title="Manpage Reachability")
    table.add_column(
        "Tool",
        style="cyan",
        max_width=_TOOL_COLUMN_MAX_WIDTH,
        no_wrap=True,
        overflow="ellipsis",
    )
    table.add_column("State", width=_STATE_COLUMN_WIDTH, no_wrap=True)
    table.add_column(
        "Source", max_width=_SOURCE_COLUMN_MAX_WIDTH, no_wrap=True, overflow="ellipsis"
    )
    table.add_column(
        "Upstream",
        width=_upstream_width(
            [cell for _, _, cell in rendered],
            _upstream_budget(
                [label for label, _, _ in rendered], source_width, terminal_width
            ),
        ),
        no_wrap=True,
        overflow="ellipsis",
    )

    for label, row, upstream in rendered:
        state = (
            f"[{_STATE_COLOR[row.state]}]{row.state.value}[/{_STATE_COLOR[row.state]}]"
        )
        table.add_row(label, state, _source_cell(row), upstream)
    return table


def _streaming_table(
    rows: list[ToolRow] | RowSnapshot,
    pending: set[int],
    *,
    terminal_width: int,
    maximum_rows: int | None = None,
    upstream_width: int | None = None,
) -> Any:
    """One fixed row per binary, or a fixed-height leading slice while it updates.

    `upstream_width` pins the Upstream column for a live table, whose widest
    value is unknowable when the skeleton is built (ADR-0025 resolves upstream
    identity after the rows exist). Omitted, the column is sized to content,
    which is correct only once every row is final.
    """
    from rich.text import Text

    if not rows:
        return Text("No tools to report.", style="yellow")
    visible_rows = rows if maximum_rows is None else rows[:maximum_rows]
    upstream_cells = [_upstream_cell(row.upstream) for row in visible_rows]
    tool_labels = [row.tool for row in rows]
    # A live table's Source content can gain an owning-package name well
    # after the skeleton is drawn (verified asynchronously per row, like
    # Upstream), so its width is the static cap here rather than content-fit
    # -- recomputing per frame would jitter the column exactly as an
    # unpinned Upstream would (see the docstring above).
    source_width = _SOURCE_COLUMN_MAX_WIDTH
    if upstream_width is None:
        upstream_width = _upstream_width(
            upstream_cells, _upstream_budget(tool_labels, source_width, terminal_width)
        )
    tool_width = _tool_column_width(tool_labels)
    table = Table(title="Manpage Reachability")
    table.add_column(
        "Tool", style="cyan", width=tool_width, no_wrap=True, overflow="ellipsis"
    )
    table.add_column(
        "State",
        width=_STATE_COLUMN_WIDTH,
        no_wrap=True,
        overflow="ellipsis",
    )
    table.add_column(
        "Source",
        width=source_width,
        no_wrap=True,
        overflow="ellipsis",
    )
    table.add_column(
        "Upstream",
        width=upstream_width,
        no_wrap=True,
        overflow="ellipsis",
    )
    for index, (row, upstream) in enumerate(
        zip(visible_rows, upstream_cells, strict=True)
    ):
        state = (
            "[dim]checking…[/dim]"
            if index in pending
            else (
                f"[{_STATE_COLOR[row.state]}]{row.state.value}[/{_STATE_COLOR[row.state]}]"
            )
        )
        table.add_row(row.tool, state, _source_cell(row), upstream)
    hidden_rows = len(rows) - len(visible_rows)
    if hidden_rows:
        table.add_row(
            Text(f"… {hidden_rows} more tools", style="dim"),
            Text(""),
            Text(""),
            Text("full table after completion", style="dim"),
        )
    return table


def _render_list(
    target_console: Any, rows: list[ToolRow], *, names: bool = False
) -> None:
    """Render as a Rich table on a terminal, or bare tool names otherwise.

    Bare names is what makes `maniac list | xargs maniac install` work: no
    table, no colour, no header, one binary per line -- plain `print`, not
    the Rich console, so nothing in a tool's name can be misread as markup,
    and never collapsed by package here, since `install` acts on binaries.
    `--names` forces the same output on a real terminal. `rows` is expected
    to already be filtered (ADR-0018: what is displayed and what is piped
    never disagree), so this renders exactly what it is given.
    """
    if names or not target_console.is_terminal:
        for tool in _bare_names(rows):
            print(tool)
        return

    if not rows:
        target_console.print("[yellow]No tools to report.[/yellow]")
        return

    target_console.print(_list_table(rows, terminal_width=target_console.size.width))


class _StreamingList:
    """One fixed Live table whose facts fill in after provider discovery."""

    def __init__(self, target_console: Any, reporter: Any) -> None:
        self._console = target_console
        self._reporter = reporter
        self._live: Live | None = None
        self._pending: set[int] = set()
        self._rows: list[ToolRow] | RowSnapshot = []
        self._last_refresh = 0.0
        self._alternate_screen = False
        self._row_limit: int | None = None
        self._terminal_width = 0
        self._upstream_width = len("Upstream")
        self._dirty = False

    def _live_row_limit(self, rows: list[ToolRow] | RowSnapshot) -> int | None:
        """Leading data-row capacity, reserving one row for overflow when needed."""
        rich_console = getattr(self._console, "_instance", self._console)
        # A one-line table row has five fixed lines: title, top border, header,
        # header border, and bottom border. Avoid rendering the whole inventory
        # just to learn that a tall table does not fit.
        full_capacity = max(1, rich_console.size.height - 5)
        if len(rows) <= full_capacity:
            return None
        return max(1, full_capacity - 1)

    def skeleton(self, rows: list[ToolRow] | RowSnapshot) -> None:
        self._reporter.stop()
        rich_console = getattr(self._console, "_instance", self._console)
        self._upstream_width = _upstream_budget(
            [row.tool for row in rows],
            _SOURCE_COLUMN_MAX_WIDTH,
            rich_console.size.width,
        )
        self._terminal_width = rich_console.size.width
        self._row_limit = self._live_row_limit(rows)
        self._alternate_screen = self._row_limit is not None
        self._live = Live(
            _streaming_table(
                rows,
                set(range(len(rows))),
                terminal_width=self._terminal_width,
                maximum_rows=self._row_limit,
                upstream_width=self._upstream_width,
            ),
            console=rich_console,
            auto_refresh=False,
            screen=self._alternate_screen,
            vertical_overflow="crop" if self._alternate_screen else "ellipsis",
        )
        self._live.start()
        self._rows = rows
        self._pending = set(range(len(rows)))
        self._dirty = False
        # Rich renders the initial frame in `start`; defer the first callback
        # refresh so it cannot immediately repaint the same geometry.
        self._last_refresh = monotonic()

    def local(
        self, rows: list[ToolRow] | RowSnapshot, index: int, upstream_pending: bool
    ) -> None:
        self._rows = rows
        if not upstream_pending:
            self._pending.discard(index)
        self._dirty = True
        self._publish(final=not self._pending)

    def upstream(self, rows: list[ToolRow] | RowSnapshot, indexes: set[int]) -> None:
        self._rows = rows
        self._pending.difference_update(indexes)
        self._dirty = True
        self._publish(final=not self._pending)

    def idle(self) -> None:
        """Flush a throttled frame while the coordinator waits on remote work."""
        self._publish()

    def _publish(self, *, final: bool = False) -> None:
        if self._live is None or not self._dirty:
            return
        now = monotonic()
        if not final and now - self._last_refresh < 0.25:
            return
        self._live.update(
            _streaming_table(
                self._rows,
                self._pending,
                terminal_width=self._terminal_width,
                maximum_rows=self._row_limit,
                # Reserved at skeleton time and held for the table's life:
                # ADR-0024's fixed geometry outranks fitting the column to
                # identities that only arrive later.
                upstream_width=self._upstream_width,
            ),
            refresh=False,
        )
        self._dirty = False
        # Completion gets Rich's one refresh in `Live.stop`; refreshing here
        # would repaint the final frame twice in rapid succession.
        if not final:
            self._live.refresh()
            self._last_refresh = now

    def stop(self, *, completed: bool = True) -> bool:
        """Stop Live and say whether the grouped final table should now be printed.

        Every live frame is ungrouped, because ADR-0024 fixes row count and
        order for the table's life. None of them survives the stop: an
        alternate screen drops its own, and a normal-screen table is made
        transient so the grouped table replaces it instead of following an
        ungrouped one down the scrollback.
        """
        if self._live is not None:
            if completed:
                # Flushed even though it is about to be discarded: the live
                # table's own contract ends with every row final.
                self._publish(final=True)
            self._live.transient = not self._alternate_screen
            self._live.stop()
            self._live = None
        return completed


class _TerminalObserver(InventoryObserver):
    """Route one inventory run's events to the progress bar and the live table.

    Both sinks are optional: a pipe gets neither, a filtered terminal call
    gets only the progress bar, and the streaming inventory view gets both.
    """

    def __init__(
        self, reporter: _ProgressReporter | None, renderer: _StreamingList | None
    ) -> None:
        self._reporter = reporter
        self._renderer = renderer
        # The skeleton hands feedback to the live table. Do not keep counting
        # a hidden progress task behind it.
        self._row_progress = reporter if renderer is None else None

    def discovery_started(self, total: int) -> None:
        if self._reporter is not None:
            self._reporter.on_phase_start(total)

    def discovery_scanned(self) -> None:
        if self._reporter is not None:
            self._reporter.on_scan()

    def rows_started(self, total: int) -> None:
        if self._row_progress is not None:
            self._row_progress.on_phase_start(total)

    def row_scanned(self) -> None:
        if self._row_progress is not None:
            self._row_progress.on_scan()

    def inventory_ready(self, rows: RowSnapshot) -> None:
        if self._renderer is not None:
            self._renderer.skeleton(rows)

    def row_classified(
        self, rows: RowSnapshot, index: int, upstream_pending: bool
    ) -> None:
        if self._renderer is not None:
            self._renderer.local(rows, index, upstream_pending)

    def upstream_group_ready(self, rows: RowSnapshot, indexes: set[int]) -> None:
        if self._renderer is not None:
            self._renderer.upstream(rows, indexes)

    def idle(self) -> None:
        if self._renderer is not None:
            self._renderer.idle()


@app.command(name="list")
def list_tools(
    ctx: typer.Context,
    tools: Annotated[
        list[str] | None,
        typer.Argument(
            help="Tools to report on. With none, every binary a provider detects."
        ),
    ] = None,
    names: Annotated[
        bool,
        typer.Option(
            "--names",
            help="Force bare tool names, one per line, even on a terminal.",
        ),
    ] = False,
    outdated: Annotated[
        bool,
        typer.Option(
            "--outdated",
            help="Only rows positively proven to document another version.",
        ),
    ] = False,
    unverified: Annotated[
        bool,
        typer.Option(
            "--unverified",
            help="Only rows whose external page cannot be proven current.",
        ),
    ] = False,
    available: Annotated[
        bool,
        typer.Option(
            "--available", help="Only rows with a free page not yet installed."
        ),
    ] = False,
    missing: Annotated[
        bool,
        typer.Option("--missing", help="Only rows with no known free page."),
    ] = False,
    managed: Annotated[
        bool,
        typer.Option(
            "--managed", help="Only rows whose reachable page MANIAC installed."
        ),
    ] = False,
) -> None:
    """Report each binary's manpage reachability states."""
    states = _selected_states(
        outdated=outdated,
        unverified=unverified,
        available=available,
        missing=missing,
    )
    verbose = bool(ctx.find_root().params.get("verbose"))
    interactive = not names and console.is_terminal
    # A filter selects final-state membership, so a provisional row could lie
    # by appearing or disappearing. Keep those calls blocking; the unfiltered
    # terminal inventory is the path that streams in place.
    streaming = interactive and not verbose and not tools and not states and not managed
    reporter = _ProgressReporter(console) if interactive else None
    renderer = _StreamingList(console, reporter) if streaming and reporter else None

    completed = False
    final_table = not streaming
    try:
        rows = compute_rows(
            tools,
            config=get_config(ctx),
            observer=_TerminalObserver(reporter, renderer),
        )
        completed = True
    finally:
        if renderer is not None:
            final_table = renderer.stop(completed=completed)
        if reporter is not None:
            reporter.stop()

    rows = _filter_rows(rows, states=states, managed=managed)
    if final_table:
        # One render for every mode, so a streamed run ends in the same
        # collapsed shape a filtered or piped one has always had.
        _render_list(console, rows, names=names)
