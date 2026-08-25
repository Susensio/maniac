import os
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from maniac.crawler import find_subcommands
from maniac.discovery import discover_repo
from maniac.docs import fetch_and_extract_docs
from maniac.pipeline import run_pipeline

app = typer.Typer(
    add_completion=False,
    help="Maniac: Scrape CLI help, extract repository docs, and synthesize elite Unix manpages.",
)
console = Console()


@app.command()
def crawl(
    cmd: Annotated[
        list[str],
        typer.Argument(help="Command and optional subcommands to crawl."),
    ],
) -> None:
    """Recursively crawl and display help for a CLI command and its subcommands."""
    tree = find_subcommands(cmd)
    for header, body in tree.items():
        console.print(f"[bold cyan]{header}[/bold cyan]")
        console.print(body)
        console.print()


@app.command()
def docs(
    tool: Annotated[
        str, typer.Argument(help="Name of the tool/binary to extract docs for.")
    ],
    cache_dir: Annotated[
        str, typer.Option(help="Cache directory for repositories.")
    ] = "data/repos",
) -> None:
    """Discover repository and extract documentation files for a tool."""
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


@app.command()
def generate(
    tool: Annotated[str, typer.Argument(help="Tool name to generate a manpage for.")],
    output_dir: Annotated[
        str, typer.Option(help="Directory to save generated manpage.")
    ] = "data/manpages",
    cache_dir: Annotated[
        str, typer.Option(help="Cache directory for repositories.")
    ] = "data/repos",
    prompt_file: Annotated[
        Path | None, typer.Option(help="Path to custom system prompt file.")
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(help="LLM model name (e.g. 'Gemini 3.7 Flash (High)')."),
    ] = None,
    install: Annotated[
        bool, typer.Option(help="Install compiled manpage to ~/.local/share/man/man1.")
    ] = False,
    dry_run: Annotated[bool, typer.Option(help="Skip LLM synthesis.")] = False,
) -> None:
    """Run end-to-end pipeline: scrape help, fetch docs, synthesize via LLM, and compile."""
    try:
        with console.status(f"[bold green]Generating manpage for {tool}..."):
            result = run_pipeline(
                tool_name=tool,
                cache_dir=cache_dir,
                output_dir=output_dir,
                prompt_file=prompt_file,
                model=model,
                install=install,
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
    except (OSError, RuntimeError) as e:
        console.print(f"[bold red]Generation failed for {tool}: {e}[/bold red]")
        raise typer.Exit(1) from e


@app.command()
def batch(
    tools: Annotated[
        list[str], typer.Argument(help="List of tool names to generate manpages for.")
    ],
    output_dir: Annotated[
        str, typer.Option(help="Directory to save generated manpages.")
    ] = "data/manpages",
    cache_dir: Annotated[
        str, typer.Option(help="Cache directory for repositories.")
    ] = "data/repos",
    model: Annotated[
        str | None,
        typer.Option(help="LLM model name (e.g. 'Gemini 3.7 Flash (High)')."),
    ] = None,
    install: Annotated[
        bool, typer.Option(help="Install compiled manpages to user manpath.")
    ] = False,
) -> None:
    """Generate manpages for multiple tools sequentially."""
    for tool in tools:
        console.print(f"\n[bold blue]=== Processing {tool} ===[/bold blue]")
        try:
            generate(
                tool=tool,
                output_dir=output_dir,
                cache_dir=cache_dir,
                prompt_file=None,
                model=model,
                install=install,
                dry_run=False,
            )
        except (OSError, RuntimeError, typer.Exit) as e:
            console.print(f"[bold red]Failed {tool}: {e}[/bold red]")


@app.command("list-missing")
def list_missing(
    bin_dir: Annotated[Path | None, typer.Option(help="Directory to inspect.")] = None,
) -> None:
    """List executables in bin_dir that lack manpages."""
    target_bin_dir = bin_dir or Path.home() / ".local" / "bin"
    if not target_bin_dir.exists():
        console.print(f"[red]Directory not found: {target_bin_dir}[/red]")
        raise typer.Exit(1)

    table = Table(title=f"Executables in {target_bin_dir} Missing Manpages")
    table.add_column("Binary", style="cyan")
    table.add_column("Symlink Target", style="magenta")
    table.add_column("Discovered Repo", style="green")

    missing_count = 0
    total_count = 0

    for item in sorted(target_bin_dir.iterdir()):
        if item.is_file() or item.is_symlink():
            total_count += 1
            res = subprocess.run(
                ["man", "-w", item.name],
                capture_output=True,
                text=True,
                check=False,
            )
            has_man = res.returncode == 0 and bool(res.stdout.strip())
            if not has_man:
                missing_count += 1
                link_target = os.readlink(item) if item.is_symlink() else "direct"
                source = discover_repo(item.name, bin_dir=target_bin_dir)
                table.add_row(item.name, link_target, source.target)

    console.print(table)
    console.print(
        f"\n[bold]{missing_count}/{total_count}[/bold] binaries lack manpages."
    )


if __name__ == "__main__":
    app()
