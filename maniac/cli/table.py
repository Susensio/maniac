"""The table frame `list` and `scan` share; each adds only its own columns.

One frame, so the two commands read as one program: the same box, the same
Tool and State columns first, the same state colours. What follows State is
each command's own evidence -- `list` compares versions, `scan` names where
a page could come from (CONTRACT.md, Commands).
"""

from typing import Any

from rich.table import Table

from ..listing.models import ActionState

# Traffic-light by what remains to be done: green needs nothing, yellow
# needs a free reinstall or install, red needs an LLM.
STATE_COLOR: dict[ActionState, str] = {
    ActionState.OK: "green",
    ActionState.UNKNOWN: "yellow",
    ActionState.OUTDATED: "yellow",
    ActionState.AVAILABLE: "yellow",
    ActionState.MISSING: "red",
    ActionState.ERROR: "bold red",
}

STATE_COLUMN_WIDTH = max(len("checking…"), *(len(state.value) for state in ActionState))
TOOL_COLUMN_MAX_WIDTH = 24


def tool_column_width(labels: list[str]) -> int:
    """Widest Tool label, capped, and never below the header."""
    return min(
        TOOL_COLUMN_MAX_WIDTH, max([len("Tool"), *(len(label) for label in labels)])
    )


def frame(title: str, *, tool_width: int | None = None) -> Table:
    """A titled table with its Tool and State columns; callers add the rest."""
    table = Table(title=title)
    table.add_column(
        "Tool",
        style="cyan",
        width=tool_width,
        max_width=TOOL_COLUMN_MAX_WIDTH,
        no_wrap=True,
        overflow="ellipsis",
    )
    table.add_column(
        "State", width=STATE_COLUMN_WIDTH, no_wrap=True, overflow="ellipsis"
    )
    return table


def state_cell(state: ActionState) -> Any:
    color = STATE_COLOR[state]
    return f"[{color}]{state.value}[/{color}]"
