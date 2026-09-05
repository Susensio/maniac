"""`crawl` and `docs`: inspect a tool's CLI help and upstream documentation."""

from typing import Annotated

import typer

from ..exceptions import ManiacError
from . import app, console, default_cfg
from .options import CacheDirOption


@app.command()
def crawl(
    cmd: Annotated[
        list[str],
        typer.Argument(help="Command and optional subcommands to crawl."),
    ],
) -> None:
    """Recursively crawl and display help for a CLI command and its subcommands."""
    from ..sources.crawler import find_subcommands

    try:
        tree = find_subcommands(cmd)
        for header, body in tree.items():
            console.print(f"[bold cyan]{header}[/bold cyan]")
            console.print(body)
            console.print()
    except ManiacError as e:
        console.print(f"[bold red]Crawl failed: {e}[/bold red]")
        raise typer.Exit(1) from e


@app.command()
def docs(
    tool: Annotated[
        str, typer.Argument(help="Name of the tool/binary to extract docs for.")
    ],
    cache_dir: CacheDirOption = str(default_cfg.cache_dir),
) -> None:
    """Discover repository and extract documentation files for a tool."""
    from ..sources.discovery import discover_repo
    from ..sources.docs import fetch_and_extract_docs

    try:
        source = discover_repo(tool)
        console.print(
            f"[bold green]Discovered repository source:[/bold green] {source.target} (local={source.is_local})"
        )
        doc_files = fetch_and_extract_docs(source, cache_dir=cache_dir)
        console.print(
            f"[bold green]Found {len(doc_files)} documentation files:[/bold green]"
        )
        for df in doc_files:
            console.print(f" - {df.rel_path} ({len(df.content)} chars)")
    except ManiacError as e:
        console.print(f"[bold red]Docs extraction failed for {tool}: {e}[/bold red]")
        raise typer.Exit(1) from e
