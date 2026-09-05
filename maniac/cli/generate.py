"""`generate`: run the end-to-end scrape/fetch/synthesize/compile pipeline."""

from pathlib import Path
from typing import Annotated

import typer

from ..exceptions import ManiacError
from . import app, console, default_cfg
from .options import (
    CacheDirOption,
    DryRunOption,
    ForceOption,
    ModelOption,
    OutputDirOption,
)


@app.command()
def generate(
    tools: Annotated[
        list[str], typer.Argument(help="List of tool names to generate manpages for.")
    ],
    output_dir: OutputDirOption = str(default_cfg.output_dir),
    cache_dir: CacheDirOption = str(default_cfg.cache_dir),
    prompt_file: Annotated[
        Path | None, typer.Option(help="Path to custom system prompt file.")
    ] = None,
    model: ModelOption = None,
    install: Annotated[
        bool, typer.Option(help="Install compiled manpage to ~/.local/share/man/man1.")
    ] = False,
    force: ForceOption = False,
    dry_run: DryRunOption = False,
) -> None:
    """Run end-to-end pipeline: scrape help, fetch docs, synthesize via LLM, and compile."""
    from ..orchestration.pipeline import run_pipeline

    failures = 0
    for tool in tools:
        if len(tools) > 1:
            console.print(f"\n[bold blue]=== Processing {tool} ===[/bold blue]")
        try:
            with console.status(f"[bold green]Generating manpage for {tool}..."):
                result = run_pipeline(
                    tool_name=tool,
                    cache_dir=cache_dir,
                    output_dir=output_dir,
                    prompt_file=prompt_file,
                    model=model,
                    install=install,
                    force=force,
                    dry_run=dry_run,
                )

            console.print(
                f"[bold green]✓ Successfully generated manpage for {tool}![/bold green]"
            )
            console.print(f" • Commands scraped: {result.command_count}")
            console.print(f" • Doc files used:   {result.doc_file_count}")
            console.print(f" • Markdown file:    {result.markdown_path}")
            if result.roff_path:
                console.print(f" • Compiled roff:    {result.roff_path}")
            if result.installed_path:
                console.print(f" • Installed at:     {result.installed_path}")
        except (OSError, RuntimeError, ManiacError) as e:
            console.print(f"[bold red]Generation failed for {tool}: {e}[/bold red]")
            failures += 1

    if failures:
        console.print(
            f"\n[bold red]{failures}/{len(tools)} tool(s) failed to generate.[/bold red]"
        )
        raise typer.Exit(1)
