"""`list` and `update`: the pages maniac installed (CONTRACT.md rules 1 and 4)."""

from pathlib import Path
from typing import Annotated, Any

import typer

from ..exceptions import ManiacError
from ..listing.models import ActionState
from . import app, console, get_config, require_login_path
from .options import DryRunOption, ModelOption

_STATE_COLOR = {
    ActionState.OK: "green",
    ActionState.OUTDATED: "yellow",
    ActionState.UNKNOWN: "yellow",
}


@app.command(name="list")
def list_pages(
    ctx: typer.Context,
    tools: Annotated[
        list[str] | None,
        typer.Argument(
            help="Tools to show. With none, every page maniac installed.",
            show_default=False,
        ),
    ] = None,
    outdated: Annotated[
        bool,
        typer.Option("--outdated", help="Only pages documenting another version."),
    ] = False,
    unknown: Annotated[
        bool,
        typer.Option(
            "--unknown", help="Only pages whose version cannot be proven either way."
        ),
    ] = False,
    names: Annotated[
        bool,
        typer.Option(
            "--names", help="Bare tool names, one per line, even on a terminal."
        ),
    ] = False,
) -> None:
    """The pages maniac installed: the version each documents, the version installed.

    `scan` shows your other tools.
    """
    from ..listing.pages import managed_pages

    require_login_path()
    rows, unmanaged = managed_pages(get_config(ctx), tools)
    wanted = {
        state
        for state, flag in (
            (ActionState.OUTDATED, outdated),
            (ActionState.UNKNOWN, unknown),
        )
        if flag
    }
    if wanted:
        rows = [row for row in rows if row.state in wanted]

    if names or not console.is_terminal:
        for row in rows:
            console.print(row.tool, markup=False, highlight=False, soft_wrap=True)
    else:
        _render(rows)
    for tool in unmanaged:
        console.print(
            f"[yellow]{tool}   maniac installed no page for it; "
            f"`maniac scan {tool}` shows its state.[/yellow]",
            soft_wrap=True,
        )
    if unmanaged:
        raise typer.Exit(1)


def _render(rows: list[Any]) -> None:
    from rich.table import Table

    if not rows:
        console.print(
            "maniac has installed no pages yet. `maniac scan` shows your tools."
        )
        return
    table = Table(box=None, pad_edge=False, show_edge=False)
    for column in ("Tool", "State", "Documents", "Installed", "Source"):
        table.add_column(column, overflow="fold")
    notes = []
    for row in rows:
        color = _STATE_COLOR.get(row.state, "red")
        table.add_row(
            row.tool,
            f"[{color}]{row.state.value}[/{color}]",
            row.documented or "-",
            row.installed or "-",
            row.source.value,
        )
        if row.copy is not None:
            notes.append(f"{row.tool}: documents {_home(row.copy)}")
        if row.note is not None:
            notes.append(f"{row.tool}: {row.note}")
    console.print(table)
    for note in notes:
        console.print(f"  [dim]{note}[/dim]", soft_wrap=True)


@app.command()
def update(
    ctx: typer.Context,
    tools: Annotated[
        list[str] | None,
        typer.Argument(
            help="Pages to update. With none, every outdated page maniac installed.",
            show_default=False,
        ),
    ] = None,
    model: ModelOption = None,
    no_synthesize: Annotated[
        bool,
        typer.Option(
            "--no-synthesize",
            help="Only reinstall from a shipped or upstream page; never call an LLM.",
        ),
    ] = False,
    dry_run: DryRunOption = False,
) -> None:
    """Reinstall each page maniac installed whose tool changed version.

    Only `outdated` pages: one whose version is `unknown` is left alone until
    you name it to `maniac install`. Each tool commits on its own, so an
    interrupted update resumes by running it again.
    """
    from ..listing.pages import managed_pages
    from ..orchestration.install import InstallRefused, run_install
    from .install import render_install

    require_login_path()
    cfg = get_config(ctx)
    rows, unmanaged = managed_pages(cfg, tools)
    failures = len(unmanaged)
    for tool in unmanaged:
        console.print(f"[yellow]{tool}   maniac installed no page for it.[/yellow]")

    outdated = [row for row in rows if row.state is ActionState.OUTDATED]
    if tools is not None:
        for row in rows:
            if row.state is ActionState.OK:
                console.print(f"{row.tool}   up to date")
            elif row.state is not ActionState.OUTDATED:
                console.print(
                    f"[yellow]{row.tool}   {row.state.value}, not updated; "
                    f"`maniac install {row.tool}` reinstalls it[/yellow]"
                )
    if not outdated:
        if tools is None:
            console.print("Every page maniac installed is up to date.")
        if failures:
            raise typer.Exit(1)
        return

    for row in outdated:
        try:
            with console.status(f"[bold green]Updating the page for {row.tool}..."):
                outcome = run_install(
                    row.tool,
                    model=model,
                    no_synthesize=no_synthesize,
                    dry_run=dry_run,
                    config=cfg,
                    copy=row.copy,
                )
            if not render_install(console, outcome, dry_run=dry_run):
                failures += 1
        except InstallRefused as e:
            console.print(f"[yellow]{row.tool}   {e}[/yellow]")
            failures += 1
        except (OSError, RuntimeError, ManiacError) as e:
            console.print(f"[bold red]Update failed for {row.tool}: {e}[/bold red]")
            failures += 1
    if failures:
        raise typer.Exit(1)


def _home(path: Path) -> str:
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)
