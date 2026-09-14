"""`source crawl` and `source docs`: inspect a tool's CLI help and upstream documentation."""

from typing import Annotated

import typer

from ..exceptions import ManiacError
from . import app, console, get_config

source_app = typer.Typer(help="Inspect a tool's CLI help and upstream documentation.")
app.add_typer(source_app, name="source")


@source_app.command()
def crawl(
    ctx: typer.Context,
    cmd: Annotated[
        list[str],
        typer.Argument(help="Command and optional subcommands to crawl."),
    ],
) -> None:
    """Recursively crawl and display help for a CLI command and its subcommands."""
    from ..sources.crawler import find_subcommands

    try:
        tree = find_subcommands(cmd, config=get_config(ctx))
        for header, body in tree.items():
            console.print(f"[bold cyan]{header}[/bold cyan]")
            console.print(body)
            console.print()
    except ManiacError as e:
        console.print(f"[bold red]Crawl failed: {e}[/bold red]")
        raise typer.Exit(1) from e


@source_app.command()
def docs(
    ctx: typer.Context,
    tool: Annotated[
        str, typer.Argument(help="Name of the tool/binary to extract docs for.")
    ],
    cache_dir: Annotated[
        str | None, typer.Option(help="Directory for cached repository clones.")
    ] = None,
) -> None:
    """Discover repository and extract documentation files for a tool."""
    from ..sources.docs import fetch_and_extract_docs
    from ..sources.documentation import documentation_source
    from ..sources.resolution import discover_repo

    try:
        cfg = get_config(ctx)
        cache_dir = cache_dir or str(cfg.cache_dir)
        source = discover_repo(tool, config=cfg)
        if source is None:
            console.print(
                f"[yellow]No installation-derived source found for '{tool}'.[/yellow]"
            )
            return
        source = documentation_source(source, cfg.documentation_repository_overrides)
        console.print(
            f"[bold green]Discovered repository source:[/bold green] {source.identity} (local={source.is_local})"
        )
        doc_files, _ = fetch_and_extract_docs(source, cache_dir=cache_dir, config=cfg)
        console.print(
            f"[bold green]Found {len(doc_files)} documentation files:[/bold green]"
        )
        for df in doc_files:
            console.print(f" - {df.rel_path} ({len(df.content)} chars)")
    except ManiacError as e:
        console.print(f"[bold red]Docs extraction failed for {tool}: {e}[/bold red]")
        raise typer.Exit(1) from e
