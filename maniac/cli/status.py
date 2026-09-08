"""`status`: report each binary's state, per ADR-0016's three-state table.

| state | action | cost |
|---|---|---|
| available | install it | zero |
| missing | synthesize | LLM |
| managed | inventory | - |

Enumeration inverts from scanning the manpath (ADR-0013/0014) to walking
providers (ADR-0016 Stage 7, `discovery.enumerate_installations`): the unit
is a binary a provider detected, not a page found on disk, since the point
is capability -- what a provider knows that the manpath cannot -- not scan
speed (measured 1.82s against 1.96s, over a fixed 0.84s of startup).

`--candidates` and its `min_words_per_flag` threshold are gone with
ADR-0016: judging whether an existing page is good enough left scope, so a
binary's state is a fact about installation, never a verdict on quality.

The unit is the binary, not the package -- upstream itself ships
`pandoc.1`, `pandoc-lua.1` and `pandoc-server.1` separately, and `man`
looks a page up by command name. Package identity groups sibling binaries
only to collapse their rows visually in the table; the bare-name pipe
below always names binaries, one per line, never a package.
"""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.table import Table

from .. import manifest
from ..config import Config
from ..sources import discovery
from ..sources.manpages import select_primary_manpage
from . import app, console, default_cfg

if TYPE_CHECKING:
    from ..models import Installation
    from ..sources.providers.base import Provider


class ActionState(Enum):
    """Which of ADR-0016's three buckets a binary falls in."""

    SHIPS_UNINSTALLED = "available"
    NO_PAGE = "missing"
    MANAGED = "managed"


_ACTION_AND_COST: dict[ActionState, tuple[str, str]] = {
    ActionState.SHIPS_UNINSTALLED: ("install it", "zero"),
    ActionState.NO_PAGE: ("synthesize", "LLM"),
    ActionState.MANAGED: ("inventory", "-"),
}


@dataclass(frozen=True, slots=True)
class StatusRow:
    """One binary's state. `package` groups siblings for the table view only."""

    tool: str
    package: str
    provider: str
    state: ActionState


def _state_for(
    provider: "Provider | None", inst: "Installation | None", tool: str, cfg: Config
) -> ActionState:
    """Decide one binary's state: the manifest wins first (ADR-0017).

    Checked before the install-root page, not after: an install already
    performed from tier 1 or 2 copies its source file verbatim (ADR-0016),
    carrying no MANIAC provenance header, so re-deriving "installed" from
    the install root every time would keep reporting it as not-yet-installed.
    The manifest, not a location-and-filename guess, is what stops a page a
    user hand-placed in `man_dir` from reporting as MANAGED. An entry whose
    file has vanished -- a crash between recording it and the copy that
    follows (ADR-0017), or the file removed by hand -- is not reported as
    MANAGED either: nothing is there to inventory, so the binary falls
    through to whatever the install root or provider can offer instead.
    """
    entry = manifest.lookup(tool, config=cfg)
    if entry is not None and entry.path.exists():
        return ActionState.MANAGED
    if provider is not None and inst is not None:
        page = select_primary_manpage(provider.local_docs(inst), inst.binary)
        if page is not None:
            return ActionState.SHIPS_UNINSTALLED
    return ActionState.NO_PAGE


def compute_status(
    tools: list[str] | None = None, config: Config | None = None
) -> list[StatusRow]:
    """One row per binary: every provider-detected installation, or exactly the named tools.

    With no names, walks `$PATH` (`discovery.enumerate_installations`) and
    reports every binary some provider claims -- the manpath is never
    scanned. With names, resolves exactly those, unfiltered; a name no
    provider claims still gets a row (`NO_PAGE`, unless MANIAC already
    manages a page for it) rather than nothing, per ADR-0013.
    """
    cfg = config or default_cfg

    if tools:
        rows: list[StatusRow] = []
        for tool in dict.fromkeys(tools):
            found = discovery.find_installation(tool)
            provider, inst = found if found else (None, None)
            rows.append(
                StatusRow(
                    tool=tool,
                    package=inst.package if inst else tool,
                    provider=provider.name if provider else "",
                    state=_state_for(provider, inst, tool, cfg),
                )
            )
        return rows

    return [
        StatusRow(
            tool=inst.binary,
            package=inst.package,
            provider=provider.name,
            state=_state_for(provider, inst, inst.binary, cfg),
        )
        for provider, inst in discovery.enumerate_installations()
    ]


def _bare_names(rows: list[StatusRow]) -> list[str]:
    """Deduplicated binary names in first-seen order, for the pipe-friendly path."""
    seen: dict[str, None] = {}
    for row in rows:
        seen.setdefault(row.tool, None)
    return list(seen)


def _grouped_for_display(rows: list[StatusRow]) -> list[tuple[str, ActionState]]:
    """Collapse binaries sharing one (provider, package, state) into one table row.

    A solo group's label is its one tool's name, unchanged. A group of
    several is the package name with a count suffix, e.g. `python (12
    binaries)`, not every sibling's name comma-joined: that joined form is
    unbounded -- a package can expose a dozen-plus binaries under one state
    -- and blows up the Tool column's width, breaking the reader's ability
    to track rows by eye down the table.

    Package identity is a display grouping only -- `pandoc`, `pandoc-lua`
    and `pandoc-server` share one install root but are three separate `man`
    lookups. Grouping on state too means one sibling already installed does
    not stop its still-uninstalled siblings from collapsing together.
    """
    groups: dict[tuple[str, str, ActionState], list[str]] = {}
    order: list[tuple[str, str, ActionState]] = []
    for row in rows:
        key = (row.provider, row.package, row.state)
        if key not in groups:
            order.append(key)
        groups.setdefault(key, []).append(row.tool)

    rendered: list[tuple[str, ActionState]] = []
    for provider, package, state in order:
        tools = groups[(provider, package, state)]
        label = tools[0] if len(tools) == 1 else f"{package} ({len(tools)} binaries)"
        rendered.append((label, state))
    return rendered


def _render_status(
    target_console: Any, rows: list[StatusRow], *, names: bool = False
) -> None:
    """Render as a Rich table on a terminal, or bare tool names otherwise.

    Bare names is what makes `maniac status | xargs maniac install` work:
    no table, no colour, no header, one binary per line -- plain `print`,
    not the Rich console, so nothing in a tool's name can be misread as
    markup, and never collapsed by package here, since `install` acts on
    binaries. `--names` forces the same output on a real terminal.
    """
    if names or not target_console.is_terminal:
        for tool in _bare_names(rows):
            print(tool)
        return

    if not rows:
        target_console.print("[yellow]No tools to report.[/yellow]")
        return

    table = Table(title="Manpage Status")
    table.add_column("Tool", style="cyan")
    table.add_column("State")

    for label, state in _grouped_for_display(rows):
        table.add_row(label, state.value)

    target_console.print(table)


@app.command()
def status(
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
) -> None:
    """Report each binary's state: available, missing, or managed."""
    rows = compute_status(tools)
    _render_status(console, rows, names=names)
