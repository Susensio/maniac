"""`list`: report each binary's manpage reachability, per ADR-0018's action ladder.

| state    | means                                                             |
|----------|--------------------------------------------------------------------|
| ok       | a page resolves through `man` now, from any source, and looks fresh |
| outdated | a page resolves, MANIAC installed it, and its recorded version no longer matches the installed binary |
| available| nothing resolves, but a page can be had without an LLM             |
| missing  | nothing resolves and no free page is known                         |

This reverses ADR-0013/ADR-0016's choice, recorded in this module's earlier
docstring as "the manpath is never scanned": `find_installed_manpage_path`
(`sources/manpages.py`, `man -w`) is now called for every row, because the
question this command answers is whether `man <tool>` works, not what
MANIAC itself has done for a binary (ADR-0018). Enumeration is still a
provider walk (`discovery.enumerate_installations`), not a manpath scan --
the unit stays a binary a provider detected, since that is still what
bounds what a bulk install could act on -- but each row's *state* is now a
reachability fact, checked against `man` directly.

`--outdated`/`--available`/`--missing` filters the State axis; `--managed`
filters the Source axis to `PageSource.MANIAC`. Filters union within an
axis and intersect across axes; no flags means no filtering. There is no
`--ok`, deliberately (ADR-0018): it would select exactly the rows needing
no action.

Upstream resolution (`git ls-remote` against an installation-derived clone
URL, tier 2 of ADR-0016) runs on every row per ADR-0018 -- a binary with no
installation-derived repository makes no network call, but one that does
is resolved regardless of what state `man` already gave it.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.progress import Progress
from rich.table import Table

from .. import manifest
from ..config import Config
from ..models import RepoSource
from ..sources import discovery
from ..sources.crawler import get_version
from ..sources.manpages import (
    _opener_for,
    find_installed_manpage_path,
    select_primary_manpage,
)
from ..sources.pathcache import resolve_cached
from . import app, console, default_cfg
from .render import _repo_cell

if TYPE_CHECKING:
    from ..models import Installation
    from ..sources.providers.base import Provider


class _ProgressReporter:
    """One combined progress bar across `list`'s two phases.

    `discovery.enumerate_installations`'s `$PATH` walk and the per-row
    reachability loop both report into this through
    `on_phase_start`/`on_scan`, sharing one task and one combined total so
    the user sees a single bar for the whole command rather than a sequence
    of two. `transient=True` clears it before the final table prints.
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


class ActionState(Enum):
    """The action-ladder state a binary's manpage reachability puts it in (ADR-0018)."""

    OK = "ok"
    OUTDATED = "outdated"
    AVAILABLE = "available"
    MISSING = "missing"


# Traffic-light by what remains to be done: green needs nothing, yellow
# needs a free reinstall or install, red needs an LLM.
_STATE_COLOR: dict[ActionState, str] = {
    ActionState.OK: "green",
    ActionState.OUTDATED: "yellow",
    ActionState.AVAILABLE: "yellow",
    ActionState.MISSING: "red",
}


class PageSource(Enum):
    """Where a reachable page came from, or would come from if installed (ADR-0018).

    One column serves both readings -- State already disambiguates which
    applies, since `ok`/`outdated` describe a page that resolves and
    `available` describes one that would if installed.
    """

    MANIAC = "maniac"
    INSTALL_ROOT = "install-root"
    SYSTEM = "system"
    NONE = ""


@dataclass(frozen=True, slots=True)
class ToolRow:
    """One binary's reachability. `package` groups siblings for the table view only."""

    tool: str
    package: str
    provider: str
    state: ActionState
    source: PageSource
    upstream: RepoSource | None


def _strip_compression(path: Path) -> Path:
    """Strip a trailing compression suffix, if `manpages._opener_for` recognizes one.

    Derives the recognized suffixes by asking `_opener_for` itself rather
    than a second hardcoded list (`manpages.py:557-569`) that could drift
    from it: a suffix is compressed exactly when `_opener_for` picks a
    decompressing opener over the plain-`open` default.
    """
    if _opener_for(path) is not open:
        return path.with_suffix("")
    return path


def _same_page(entry_path: Path, installed_path: Path) -> bool:
    """Whether a manifest entry and a `man -w` result name the same page.

    `man -w` may return a compressed path where the manifest recorded an
    uncompressed one (or vice versa), and either side may be a symlink, so
    both are resolved (`pathcache.resolve_cached`) and stripped of a
    compression suffix before comparison.
    """
    return _strip_compression(resolve_cached(entry_path)) == _strip_compression(
        resolve_cached(installed_path)
    )


def _under_root(path: Path, root: Path) -> bool:
    """Whether a resolved page path sits under an installation's root."""
    try:
        return resolve_cached(path).is_relative_to(resolve_cached(root))
    except (OSError, ValueError):
        return False


def _classify(
    provider: "Provider | None", inst: "Installation | None", tool: str, cfg: Config
) -> tuple[ActionState, PageSource]:
    """Decide one binary's state and page source by whether `man` resolves it (ADR-0018).

    Reverses `status`'s prior rule of consulting the manifest alone: `man
    -w` is checked first, and the manifest is consulted only to tell a
    MANIAC-owned page apart from one `man` would resolve regardless (a
    distro page, or one a user hand-placed). A manifest entry whose file
    exists but which `man` does not resolve is therefore no longer reported
    MANIAC-owned -- ownership requires reachability now, not only a record.
    """
    installed = find_installed_manpage_path("man", tool)
    entry = manifest.lookup(tool, config=cfg)
    owned = (
        entry is not None
        and entry.path.exists()
        and installed is not None
        and _same_page(entry.path, installed)
    )

    if installed is not None:
        if owned:
            source = PageSource.MANIAC
        elif inst is not None and _under_root(installed, inst.root):
            source = PageSource.INSTALL_ROOT
        else:
            source = PageSource.SYSTEM

        # An unclaimed binary (no provider, no `Installation`) has nothing
        # to compare `entry.version` against by default -- ask the binary
        # itself (ADR-0020). Gated on the cheap checks first: only a row
        # that is owned, carries a recorded version, and has no
        # `Installation` may pay for the subprocess this triggers.
        if owned and entry is not None and entry.version is not None and inst is None:
            current_version = get_version([tool])
        else:
            current_version = inst.version if inst is not None else None

        # `outdated` requires positive evidence -- a recorded version that
        # differs from the installed binary's current one. Absent that
        # evidence (an unowned page, or `local_lib`'s permanent lack of a
        # version) a row reads `ok` (ADR-0018): MANIAC is not entitled to
        # call a page stale it never assessed.
        outdated = (
            owned
            and entry is not None
            and entry.version is not None
            and current_version is not None
            and entry.version != current_version
        )
        return (ActionState.OUTDATED if outdated else ActionState.OK, source)

    if provider is not None and inst is not None:
        page = select_primary_manpage(provider.local_docs(inst), inst.binary)
        if page is not None:
            return (ActionState.AVAILABLE, PageSource.INSTALL_ROOT)
    return (ActionState.MISSING, PageSource.NONE)


def _resolve_upstream(
    provider: "Provider | None", inst: "Installation | None"
) -> RepoSource | None:
    """Resolve `inst`'s upstream repository."""
    if provider is None or inst is None:
        return None
    return provider.resolve_source(inst)


def compute_rows(
    tools: list[str] | None = None,
    config: Config | None = None,
    *,
    on_discovery_start: Callable[[int], None] | None = None,
    on_discovery_scan: Callable[[], None] | None = None,
    on_row_start: Callable[[int], None] | None = None,
    on_row_scan: Callable[[], None] | None = None,
) -> list[ToolRow]:
    """One row per binary: every provider-detected installation, or exactly the named tools.

    With no names, walks `$PATH` (`discovery.enumerate_installations`) and
    reports every binary some provider claims. With names, resolves
    exactly those, unfiltered; a name no provider claims still gets a row
    (`MISSING`, unless `man` or the manifest says otherwise) rather than
    nothing, per ADR-0013.

    The four `on_*` callbacks, all `None` by default, are purely additive
    instrumentation for a caller with a console in scope (the CLI command);
    every other caller, including tests, omits them and sees no behaviour
    change. `on_discovery_*` passes straight through to
    `discovery.enumerate_installations` (skipped entirely on the `tools`
    path, which never calls it); `on_row_*` wraps this function's own
    per-row `_classify` loop, whichever path runs it.

    Sequential by design: `man -w` measured ~44ms per lookup, so ~66
    binaries adds ~2.9s on top of enumeration -- accepted for this pass
    rather than batched, since `man -w a b c` only prints found pages and
    their basenames do not map back to query names.
    """
    cfg = config or default_cfg

    if tools:
        unique_tools = list(dict.fromkeys(tools))
        if on_row_start is not None:
            on_row_start(len(unique_tools))
        rows: list[ToolRow] = []
        for tool in unique_tools:
            # No `discovery.discover_repo(tool)` fallback when `found` is
            # None: it shares `find_installation`'s own bin-path resolution
            # and provider registry lookup verbatim, so it would only
            # reproduce the same failed detection, never find anything new.
            found = discovery.find_installation(tool)
            provider, inst = found if found else (None, None)
            state, source = _classify(provider, inst, tool, cfg)
            rows.append(
                ToolRow(
                    tool=tool,
                    package=inst.package if inst else tool,
                    provider=provider.name if provider else "",
                    state=state,
                    source=source,
                    upstream=_resolve_upstream(provider, inst),
                )
            )
            if on_row_scan is not None:
                on_row_scan()
        return rows

    installations = discovery.enumerate_installations(
        on_start=on_discovery_start, on_scan=on_discovery_scan
    )
    if on_row_start is not None:
        on_row_start(len(installations))
    rows = []
    for provider, inst in installations:
        state, source = _classify(provider, inst, inst.binary, cfg)
        rows.append(
            ToolRow(
                tool=inst.binary,
                package=inst.package,
                provider=provider.name,
                state=state,
                source=source,
                upstream=_resolve_upstream(provider, inst),
            )
        )
        if on_row_scan is not None:
            on_row_scan()
    return rows


def _bare_names(rows: list[ToolRow]) -> list[str]:
    """Deduplicated binary names in first-seen order, for the pipe-friendly path."""
    seen: dict[str, None] = {}
    for row in rows:
        seen.setdefault(row.tool, None)
    return list(seen)


def _upstream_key(upstream: RepoSource | None) -> tuple[str, bool] | None:
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
    return (upstream.target, upstream.is_local)


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


def _filter_rows(
    rows: list[ToolRow],
    *,
    outdated: bool,
    available: bool,
    missing: bool,
    managed: bool,
) -> list[ToolRow]:
    """Narrow `rows` by the State axis (union) and the Source axis (union), intersected.

    No flag set at all means no filtering. `--available --missing` unions
    within the State axis to every row worth acting on; `--managed
    --outdated` intersects across axes to MANIAC's own stale pages. There
    is no `--ok` flag (ADR-0018): it would select exactly the rows needing
    no action.
    """
    state_axis = {
        state
        for state, flag in (
            (ActionState.OUTDATED, outdated),
            (ActionState.AVAILABLE, available),
            (ActionState.MISSING, missing),
        )
        if flag
    }
    source_axis = {PageSource.MANIAC} if managed else set()

    if not state_axis and not source_axis:
        return rows
    return [
        row
        for row in rows
        if (not state_axis or row.state in state_axis)
        and (not source_axis or row.source in source_axis)
    ]


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

    table = Table(title="Manpage Reachability")
    table.add_column("Tool", style="cyan")
    table.add_column("State")
    table.add_column("Source")
    table.add_column("Upstream")

    for label, row in _grouped_for_display(rows):
        table.add_row(
            label,
            f"[{_STATE_COLOR[row.state]}]{row.state.value}[/{_STATE_COLOR[row.state]}]",
            row.source.value,
            _upstream_cell(row.upstream),
        )

    target_console.print(table)


@app.command(name="list")
def list_tools(
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
            "--outdated", help="Only rows whose page MANIAC owns and is stale."
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
    """Report each binary's manpage reachability: ok, outdated, available, or missing."""
    interactive = not names and console.is_terminal
    reporter = _ProgressReporter(console) if interactive else None
    try:
        rows = compute_rows(
            tools,
            on_discovery_start=reporter.on_phase_start if reporter else None,
            on_discovery_scan=reporter.on_scan if reporter else None,
            on_row_start=reporter.on_phase_start if reporter else None,
            on_row_scan=reporter.on_scan if reporter else None,
        )
    finally:
        if reporter is not None:
            reporter.stop()
    rows = _filter_rows(
        rows, outdated=outdated, available=available, missing=missing, managed=managed
    )
    _render_list(console, rows, names=names)
