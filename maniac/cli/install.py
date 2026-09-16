"""`install`: resolve a manpage through ADR-0016's tiers and install it.

The inverse of `uninstall`, per ADR-0016's rename from `generate`. Tries the
install root, then the upstream repository with the version matched, then
LLM synthesis, in that order; `--no-synthesize` restricts it to the first
two tiers.
"""

from typing import Annotated, Any

import typer

from ..exceptions import ManiacError
from ..orchestration.install import InstallOutcome, InstallRefused
from . import app, console, get_config
from .options import (
    DryRunOption,
    ForceOption,
    ModelOption,
)


def _render_install(target_console: Any, outcome: InstallOutcome) -> None:
    from rich.markup import escape

    # `outcome.detail` carries literal "[no synthesis]" -- escaped so Rich's
    # markup parser doesn't read it as an (invalid, silently dropped) style tag.
    detail = escape(outcome.detail)
    if outcome.tier is None:
        target_console.print(f"[yellow]{outcome.tool}   {detail}[/yellow]")
        return
    target_console.print(f"[bold green]{outcome.tool}[/bold green]   {detail}")


@app.command()
def install(
    ctx: typer.Context,
    tools: Annotated[
        list[str] | None,
        typer.Argument(help="List of tool names to install manpages for."),
    ] = None,
    model: ModelOption = None,
    no_synthesize: Annotated[
        bool,
        typer.Option(
            "--no-synthesize",
            help=(
                "Restrict to tiers 1-2 (install root, repository); never calls an LLM."
            ),
        ),
    ] = False,
    force: ForceOption = False,
    dry_run: DryRunOption = False,
) -> None:
    """Install a manpage: install root, then repository, then LLM synthesis.

    Zero tool names exits quietly rather than raising Typer's missing-argument
    error, since `$(maniac status)` can legitimately expand to nothing.
    """
    if not tools:
        return

    cfg = get_config(ctx)

    from ..orchestration.install import run_install

    failures = 0
    for tool in tools:
        if len(tools) > 1:
            console.print(f"\n[bold blue]=== Processing {tool} ===[/bold blue]")
        try:
            with console.status(f"[bold green]Installing manpage for {tool}..."):
                outcome = run_install(
                    tool,
                    model=model,
                    no_synthesize=no_synthesize,
                    force=force,
                    dry_run=dry_run,
                    config=cfg,
                )
            _render_install(console, outcome)
        except InstallRefused as e:
            # A refusal, not a failure: no tier ran, so nothing failed --
            # rendered like the tier=None case above, not like an error.
            console.print(f"[yellow]{tool}   {e}[/yellow]")
        except (OSError, RuntimeError, ManiacError) as e:
            console.print(f"[bold red]Install failed for {tool}: {e}[/bold red]")
            failures += 1

    if failures:
        console.print(
            f"\n[bold red]{failures}/{len(tools)} tool(s) failed to install.[/bold red]"
        )
        raise typer.Exit(1)
