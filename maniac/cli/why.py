"""`why <tool>`: every decision maniac makes about one tool (CONTRACT.md rule 3)."""

from typing import Annotated

import typer

from ..config import Config
from ..exceptions import ManiacError
from . import app, console, get_config, require_login_path


@app.command()
def why(
    ctx: typer.Context,
    tool: Annotated[
        str, typer.Argument(help="The tool to explain.", show_default=False)
    ],
    help_text: Annotated[
        bool,
        typer.Option(
            "--help-text",
            help="Also print the --help output a generated page would be written from.",
        ),
    ] = False,
    docs: Annotated[
        bool,
        typer.Option(
            "--docs",
            help="Also list the repository documents a generated page would use.",
        ),
    ] = False,
) -> None:
    """Explain every decision maniac makes about a tool, and what `install` would do."""
    from rich.markup import escape

    from ..orchestration.why import explain

    require_login_path()
    cfg = get_config(ctx)
    explanation = explain(tool, cfg)
    width = max([len("install"), *(len(s.title) for s in explanation.sections)])
    for section in explanation.sections:
        for index, line in enumerate(section.lines):
            label = section.title if index == 0 else ""
            console.print(
                f"[bold]{label:<{width}}[/bold]  {escape(line)}", soft_wrap=True
            )
    console.print(f"[bold]{'install':<{width}}[/bold]  {escape(explanation.verdict)}")
    if help_text:
        _print_help_text(tool, cfg)
    if docs:
        _print_docs(tool, cfg)


def _print_help_text(tool: str, cfg: Config) -> None:
    from ..orchestration.context import resolve_tool
    from ..sources.crawler import find_subcommands

    console.print()
    try:
        resolved = resolve_tool(tool, config=cfg)
        tree = find_subcommands(
            [resolved.command], config=resolved.config, executable=resolved.executable
        )
    except ManiacError as e:
        console.print(f"[bold red]Could not read its help: {e}[/bold red]")
        raise typer.Exit(1) from e
    for header, body in tree.items():
        console.print(f"[bold cyan]{header}[/bold cyan]", markup=True)
        console.print(body, markup=False, highlight=False)
        console.print()


def _print_docs(tool: str, cfg: Config) -> None:
    from ..orchestration.context import resolve_tool
    from ..sources.docs import fetch_and_extract_docs

    console.print()
    try:
        resolved = resolve_tool(tool, config=cfg)
        source = resolved.documentation_source
        if source is None:
            console.print("No repository is known for it, so there are no documents.")
            return
        doc_files, matched = fetch_and_extract_docs(
            source,
            cache_dir=resolved.cache_dir,
            max_total_chars=resolved.config.max_total_doc_chars,
            config=resolved.config,
            version=resolved.installed_version,
        )
    except ManiacError as e:
        console.print(f"[bold red]Could not read its documents: {e}[/bold red]")
        raise typer.Exit(1) from e
    at = "at the installed version" if matched else "from the default branch"
    console.print(f"{len(doc_files)} documents from {source.identity}, {at}:")
    for doc in doc_files:
        console.print(f"  {doc.rel_path} ({len(doc.content)} characters)", markup=False)
