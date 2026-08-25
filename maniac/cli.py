"""Maniac CLI entry point."""

import os
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from maniac.config import Config
from maniac.eval import evaluate_manpage
from maniac.exceptions import ManiacError
from maniac.orchestration.pipeline import run_pipeline
from maniac.sources.crawler import find_subcommands
from maniac.sources.discovery import discover_repo
from maniac.sources.docs import fetch_and_extract_docs

app = typer.Typer(
    add_completion=False,
    help="Maniac: Scrape CLI help, extract repository docs, and synthesize elite Unix manpages.",
)
console = Console()
default_cfg = Config()


@app.command()
def crawl(
    cmd: Annotated[
        list[str],
        typer.Argument(help="Command and optional subcommands to crawl."),
    ],
) -> None:
    """Recursively crawl and display help for a CLI command and its subcommands."""
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
    cache_dir: Annotated[
        str, typer.Option(help="Cache directory for repositories.")
    ] = str(default_cfg.cache_dir),
) -> None:
    """Discover repository and extract documentation files for a tool."""
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


@app.command()
def generate(
    tool: Annotated[str, typer.Argument(help="Tool name to generate a manpage for.")],
    output_dir: Annotated[
        str, typer.Option(help="Directory to save generated manpage.")
    ] = str(default_cfg.output_dir),
    cache_dir: Annotated[
        str, typer.Option(help="Cache directory for repositories.")
    ] = str(default_cfg.cache_dir),
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
    except (OSError, RuntimeError, ManiacError) as e:
        console.print(f"[bold red]Generation failed for {tool}: {e}[/bold red]")
        raise typer.Exit(1) from e


@app.command()
def batch(
    tools: Annotated[
        list[str], typer.Argument(help="List of tool names to generate manpages for.")
    ],
    output_dir: Annotated[
        str, typer.Option(help="Directory to save generated manpages.")
    ] = str(default_cfg.output_dir),
    cache_dir: Annotated[
        str, typer.Option(help="Cache directory for repositories.")
    ] = str(default_cfg.cache_dir),
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
        except (OSError, RuntimeError, ManiacError, typer.Exit) as e:
            console.print(f"[bold red]Failed {tool}: {e}[/bold red]")


@app.command("eval")
def eval_cmd(
    tool: Annotated[str, typer.Argument(help="Name of the tool to evaluate.")],
    manpage_file: Annotated[
        Path | None,
        typer.Option(
            help="Path to manpage Markdown file (default: data/manpages/<tool>.1.md)."
        ),
    ] = None,
    context_file: Annotated[
        Path | None,
        typer.Option(
            help="Path to context file (default: data/intermediate/<tool>_context.md)."
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(help="LLM model name (e.g. 'Gemini 3.7 Flash (High)')."),
    ] = None,
    min_score: Annotated[
        int,
        typer.Option(help="Minimum passing score threshold (0-100)."),
    ] = 70,
) -> None:
    """Evaluate quality of a generated manpage using deterministic checks and LLM-as-a-Judge."""
    target_manpage = manpage_file or Path(f"data/manpages/{tool}.1.md")
    target_context = context_file or Path(f"data/intermediate/{tool}_context.md")

    if not target_manpage.exists():
        console.print(
            f"[bold red]Error: Manpage not found at {target_manpage}[/bold red]"
        )
        raise typer.Exit(1)

    if not target_context.exists():
        console.print(
            f"[bold red]Error: Context file not found at {target_context}[/bold red]"
        )
        raise typer.Exit(1)

    manpage_text = target_manpage.read_text(encoding="utf-8")
    context_text = target_context.read_text(encoding="utf-8")

    try:
        with console.status(f"[bold green]Evaluating manpage for {tool}..."):
            result = evaluate_manpage(
                tool_name=tool,
                manpage_text=manpage_text,
                context_text=context_text,
                model=model,
                pass_threshold=min_score,
            )

        # Output Rich evaluation table
        table = Table(title=f"Quality Evaluation: {tool} ({result.score}/100)")
        table.add_column("Rubric Category", style="cyan")
        table.add_column("Score", justify="right", style="magenta")
        table.add_column("Max", justify="right", style="dim")

        rubric_labels = {
            "domain_ontology": "Domain Ontology & Architecture",
            "formatting": "Flag & Command Formatting",
            "subsystem_grouping": "Subsystem Grouping",
            "environment_files_exit": "Environment / Files / Exit Status",
            "workflow_examples": "Workflow Examples",
        }

        for key, label in rubric_labels.items():
            cat_score = result.rubric_breakdown.get(key, 0)
            table.add_row(label, str(cat_score), "20")

        table.add_section()
        status_str = (
            "[bold green]PASSED[/bold green]"
            if result.passed
            else "[bold red]FAILED[/bold red]"
        )
        table.add_row("Total Score", f"[bold]{result.score}[/bold]", "100")
        table.add_row("Status", status_str, "")

        console.print(table)

        if result.summary:
            console.print(f"\n[bold]Summary:[/bold] {result.summary}")

        if result.defects:
            console.print("\n[bold yellow]Defects & Recommendations:[/bold yellow]")
            for defect in result.defects:
                console.print(f" • [yellow]{defect}[/yellow]")

        if not result.passed:
            console.print(
                f"\n[bold red]Evaluation failed for {tool} (Score: {result.score}/100)[/bold red]"
            )
            raise typer.Exit(1)

        console.print(
            f"\n[bold green]✓ Evaluation passed for {tool} with score {result.score}/100![/bold green]"
        )

    except typer.Exit:
        raise
    except (OSError, RuntimeError, ManiacError) as e:
        console.print(f"[bold red]Evaluation error for {tool}: {e}[/bold red]")
        raise typer.Exit(1) from e


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
