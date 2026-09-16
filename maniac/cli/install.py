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


def _no_page_installed(outcome: InstallOutcome, *, dry_run: bool) -> bool:
    """Whether `outcome` leaves this tool with no page, real or previewed (ADR-0048).

    A dry run never sets `installed_path` by design -- it previews rather
    than installs -- so `tier` is the verdict there: `None` means no tier
    found anything to preview, the dry-run equivalent of no page. A real
    run's verdict is `installed_path` directly, since a tier can be found
    (`tier` set) and still fail to land a page, as tier 3 does when pandoc
    is missing or rejects the markdown.
    """
    if dry_run:
        return outcome.tier is None
    return outcome.installed_path is None


def _render_install(
    target_console: Any, outcome: InstallOutcome, *, dry_run: bool
) -> None:
    from rich.markup import escape

    # `outcome.detail` carries literal "[no synthesis]" -- escaped so Rich's
    # markup parser doesn't read it as an (invalid, silently dropped) style tag.
    detail = escape(outcome.detail)
    if _no_page_installed(outcome, dry_run=dry_run):
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
            _render_install(console, outcome, dry_run=dry_run)
            if _no_page_installed(outcome, dry_run=dry_run):
                # No page landed on disk for this tool -- whether tiers 1-2
                # found nothing under --no-synthesize, or tier 3 reached
                # synthesis but pandoc was missing or rejected the markdown
                # (`installed_path` stays None either way) -- so it counts
                # toward the exit status (ADR-0048).
                failures += 1
        except InstallRefused as e:
            # A refusal still leaves this tool with no page, so it counts
            # toward the exit status like any other outcome without one
            # (ADR-0048) -- only the exit status changes, not the yellow,
            # reason-naming presentation ADR-0020 gave refusals.
            console.print(f"[yellow]{tool}   {e}[/yellow]")
            failures += 1
        except (OSError, RuntimeError, ManiacError) as e:
            console.print(f"[bold red]Install failed for {tool}: {e}[/bold red]")
            failures += 1

    if failures:
        console.print(
            f"\n[bold red]{failures}/{len(tools)} tool(s) did not install.[/bold red]"
        )
        raise typer.Exit(1)
