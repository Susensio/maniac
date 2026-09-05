"""`uninstall`: remove a MANIAC-generated manpage and restore any vendor backup."""

from dataclasses import dataclass
from typing import Annotated, Any

import typer

from ..config import Config
from ..exceptions import ManiacError
from ..installer import UninstallResult
from . import app, console, default_cfg


@dataclass(frozen=True, slots=True)
class UninstallOutcome:
    """Result of computing `uninstall`: the tool it was asked for plus the outcome."""

    tool: str
    result: UninstallResult


def compute_uninstall(
    tool: str, purge: bool = False, config: Config | None = None
) -> UninstallOutcome:
    """Uninstall a MANIAC-managed manpage and report what happened."""
    from ..installer import uninstall_manpage

    return UninstallOutcome(
        tool=tool,
        result=uninstall_manpage(tool, purge=purge, config=config or default_cfg),
    )


def _render_uninstall(target_console: Any, outcome: UninstallOutcome) -> None:
    result = outcome.result
    if not result.removed and result.foreign_kept is None:
        target_console.print(
            f"[yellow]No installed manpage found for '{outcome.tool}'.[/yellow]"
        )
        return

    if result.removed:
        target_console.print(
            f"[bold green]✓ Uninstalled manpage for {outcome.tool}![/bold green]"
        )
        for p in result.removed:
            target_console.print(f" • Removed: {p}")

    if result.foreign_kept is not None:
        target_console.print(
            f"[yellow]⚠ Left non-MANIAC manpage in place at "
            f"{result.foreign_kept} (not ours to remove).[/yellow]"
        )


@app.command("uninstall")
def uninstall_cmd(
    tool: Annotated[str, typer.Argument(help="Tool name to uninstall.")],
    purge: Annotated[
        bool,
        typer.Option(
            "--purge",
            help="Also delete generated Markdown and intermediate context files.",
        ),
    ] = False,
) -> None:
    """Uninstall a MANIAC-generated manpage and restore vendor backup if present."""
    try:
        outcome = compute_uninstall(tool, purge=purge)
    except (OSError, RuntimeError, ManiacError) as e:
        console.print(
            f"[bold red]Error uninstalling manpage for {tool}: {e}[/bold red]"
        )
        raise typer.Exit(1) from e

    _render_uninstall(console, outcome)
