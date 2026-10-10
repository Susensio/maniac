"""`remove`: take out the pages maniac installed (CONTRACT.md rule 1).

Each tool's whole release goes, with everything maniac made for it (its
generated source and context), and any page it had replaced comes back.
A page maniac did not install is never touched.
"""

from dataclasses import dataclass
from typing import Annotated, Any

import typer

from ..config import Config
from ..exceptions import ManiacError
from ..installer import UninstallRefused, UninstallResult
from . import app, console, get_config


@dataclass(frozen=True, slots=True)
class RemoveOutcome:
    """What `remove` did for one tool."""

    tool: str
    result: UninstallResult


def compute_remove(tool: str, config: Config | None = None) -> RemoveOutcome:
    from ..installer import uninstall_manpage

    return RemoveOutcome(
        tool=tool, result=uninstall_manpage(tool, config=config or Config())
    )


def _render_remove(target_console: Any, outcome: RemoveOutcome) -> bool:
    """Print one tool's outcome; return whether maniac had a page for it."""
    from rich.markup import escape

    result = outcome.result
    tool = escape(outcome.tool)
    if (
        not result.removed
        and result.foreign_kept is None
        and not result.modified_kept
        and not result.restored
    ):
        target_console.print(
            f"[yellow]{tool}   maniac installed no page for it.[/yellow]",
            soft_wrap=True,
        )
        return False

    if result.removed or result.restored:
        target_console.print(f"[bold green]{tool}[/bold green]   removed")
    lines = [f"removed {p}" for p in result.removed]
    lines += [f"restored the page it had replaced: {p}" for p in result.restored]
    lines += [
        f"removed {p}, though it had been edited since it was installed"
        for p in result.changed
    ]
    for line in lines:
        target_console.print(f"  [dim]{escape(line)}[/dim]", soft_wrap=True)
    if result.foreign_kept is not None:
        target_console.print(
            f"  [yellow]left {escape(str(result.foreign_kept))} in place: "
            "maniac did not install it[/yellow]",
            soft_wrap=True,
        )
    for kept in result.modified_kept:
        target_console.print(
            f"  [yellow]left {escape(str(kept))} in place: it no longer points "
            "where maniac left it, so it may not be maniac's page anymore[/yellow]",
            soft_wrap=True,
        )
    return True


@app.command("remove")
def remove_cmd(
    ctx: typer.Context,
    tools: Annotated[
        list[str],
        typer.Argument(help="Tools whose pages to remove.", show_default=False),
    ],
) -> None:
    """Remove the pages maniac installed, and restore any page each replaced."""
    config = get_config(ctx)
    failed = False
    for tool in dict.fromkeys(tools):
        try:
            outcome = compute_remove(tool, config=config)
        except UninstallRefused as e:
            # Nothing was removed: a refusal, not an error (ADR-0053).
            console.print(f"[yellow]{e}[/yellow]", soft_wrap=True)
            failed = True
            continue
        except (OSError, RuntimeError, ManiacError) as e:
            console.print(f"[bold red]{tool}   could not be removed: {e}[/bold red]")
            failed = True
            continue
        failed |= not _render_remove(console, outcome)
    if failed:
        raise typer.Exit(1)
