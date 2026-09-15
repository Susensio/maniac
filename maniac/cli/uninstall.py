"""`uninstall`: remove a MANIAC-managed manpage and restore any vendor backup."""

from dataclasses import dataclass
from typing import Annotated, Any

import typer

from ..config import Config
from ..exceptions import ManiacError
from ..installer import UninstallResult
from . import app, console, get_config
from .options import ForceOption


@dataclass(frozen=True, slots=True)
class UninstallOutcome:
    """Result of computing `uninstall`: the tool it was asked for plus the outcome."""

    tool: str
    result: UninstallResult


def compute_uninstall(
    tool: str, purge: bool = False, force: bool = False, config: Config | None = None
) -> UninstallOutcome:
    """Uninstall a MANIAC-managed manpage and report what happened."""
    from ..installer import uninstall_manpage

    return UninstallOutcome(
        tool=tool,
        result=uninstall_manpage(
            tool, purge=purge, force=force, config=config or Config()
        ),
    )


def _render_uninstall(target_console: Any, outcome: UninstallOutcome) -> None:
    result = outcome.result
    if not result.removed and result.foreign_kept is None and not result.modified_kept:
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

    for kept in result.modified_kept:
        target_console.print(
            f"[yellow]⚠ Left {kept} in place: MANIAC installed "
            f"it, but its bytes have changed since. Use --force to remove it "
            f"anyway.[/yellow]"
        )


@app.command("uninstall")
def uninstall_cmd(
    ctx: typer.Context,
    tool: Annotated[str, typer.Argument(help="Tool name to uninstall.")],
    purge: Annotated[
        bool,
        typer.Option(
            "--purge",
            help="Also delete generated Markdown and intermediate context files.",
        ),
    ] = False,
    force: ForceOption = False,
) -> None:
    """Uninstall a MANIAC-managed manpage and restore vendor backup if present."""
    try:
        outcome = compute_uninstall(
            tool, purge=purge, force=force, config=get_config(ctx)
        )
    except (OSError, RuntimeError, ManiacError) as e:
        console.print(
            f"[bold red]Error uninstalling manpage for {tool}: {e}[/bold red]"
        )
        raise typer.Exit(1) from e

    _render_uninstall(console, outcome)
