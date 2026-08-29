"""Maniac CLI entry point."""

from pathlib import Path
from typing import Annotated, Any

import typer

from .config import Config
from .exceptions import ManiacError

app = typer.Typer(
    help="Maniac: Scrape CLI help, extract repository docs, and synthesize elite Unix manpages.",
)


class _LazyConsole:
    _instance: Any = None

    def __getattr__(self, name: str) -> Any:
        if self._instance is None:
            from rich.console import Console

            self._instance = Console()
        return getattr(self._instance, name)


console = _LazyConsole()
default_cfg = Config()


def _render_eval_table(target_console: Any, tool: str, result: Any) -> None:
    """Render and display quality evaluation results in a formatted Rich table."""
    from rich.table import Table

    table = Table(title=f"Quality Evaluation: {tool} ({result.score}/100)")
    table.add_column("Rubric Category", style="cyan")
    table.add_column("Score", justify="right", style="magenta")
    table.add_column("Max", justify="right", style="dim")

    rubric_labels = {
        "domain_ontology": "Domain Ontology & Architecture",
        "correctness_coverage": "Command & Flag Correctness & Coverage",
        "formatting": "Flag & Command Formatting",
        "subsystem_grouping": "Subsystem Grouping",
        "environment_reference_examples": "Environment, Reference & Examples",
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

    target_console.print(table)

    if getattr(result, "coverage", None):
        cov = result.coverage
        cmd_total = len(cov.found_cmds) + len(cov.missing_cmds)
        flag_total = len(cov.found_flags) + len(cov.missing_flags)
        target_console.print(
            f"\n[dim]CLI Coverage: "
            f"Subcommands {cov.cmd_pct:.1f}% ({len(cov.found_cmds)}/{cmd_total}) | "
            f"Flags {cov.flag_pct:.1f}% ({len(cov.found_flags)}/{flag_total})[/dim]"
        )

    if result.summary:
        target_console.print(f"\n[bold]Summary:[/bold] {result.summary}")

    if result.defects:
        target_console.print("\n[bold yellow]Defects & Recommendations:[/bold yellow]")
        for defect in result.defects:
            target_console.print(f" • [yellow]{defect}[/yellow]")


@app.callback()
def main(
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            envvar="MANIAC_VERBOSE",
            help="Enable verbose debug logging.",
        ),
    ] = False,
) -> None:
    """Initialize CLI logging settings."""
    from .logging import setup_logging

    setup_logging(verbose=verbose)


@app.command()
def crawl(
    cmd: Annotated[
        list[str],
        typer.Argument(help="Command and optional subcommands to crawl."),
    ],
) -> None:
    """Recursively crawl and display help for a CLI command and its subcommands."""
    from .sources.crawler import find_subcommands

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
    from .sources.discovery import discover_repo
    from .sources.docs import fetch_and_extract_docs

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
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Force overwrite of foreign manpages with automatic backup.",
        ),
    ] = False,
    dry_run: Annotated[bool, typer.Option(help="Skip LLM synthesis.")] = False,
) -> None:
    """Run end-to-end pipeline: scrape help, fetch docs, synthesize via LLM, and compile."""
    from .orchestration.pipeline import run_pipeline

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
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Force overwrite of foreign manpages with automatic backup.",
        ),
    ] = False,
) -> None:
    """Generate manpages for multiple tools sequentially."""
    from .orchestration.pipeline import run_pipeline

    failures = 0
    for tool in tools:
        console.print(f"\n[bold blue]=== Processing {tool} ===[/bold blue]")
        try:
            with console.status(f"[bold green]Generating manpage for {tool}..."):
                result = run_pipeline(
                    tool_name=tool,
                    cache_dir=cache_dir,
                    output_dir=output_dir,
                    prompt_file=None,
                    model=model,
                    install=install,
                    force=force,
                    dry_run=False,
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
            console.print(f"[bold red]Failed {tool}: {e}[/bold red]")
            failures += 1

    if failures:
        console.print(
            f"\n[bold red]{failures}/{len(tools)} tool(s) failed to generate.[/bold red]"
        )
        raise typer.Exit(1)


@app.command("eval")
def eval_cmd(
    tool: Annotated[str, typer.Argument(help="Name of the tool to evaluate.")],
    manpage_file: Annotated[
        Path | None,
        typer.Option(
            help="Path to manpage Markdown file (default: $XDG_DATA_HOME/maniac/manpages/<tool>.1.md)."
        ),
    ] = None,
    context_file: Annotated[
        Path | None,
        typer.Option(
            help="Path to context file (default: $XDG_STATE_HOME/maniac/intermediate/<tool>_context.md)."
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
    target_manpage = (
        manpage_file
        if manpage_file is not None
        else default_cfg.output_dir / f"{tool}.1.md"
    )
    target_context = (
        context_file
        if context_file is not None
        else default_cfg.intermediate_dir / f"{tool}_context.md"
    )

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

    from .evaluation.judge import evaluate_manpage

    try:
        with console.status(f"[bold green]Evaluating manpage for {tool}..."):
            result = evaluate_manpage(
                tool_name=tool,
                manpage_text=manpage_text,
                context_text=context_text,
                model=model,
                pass_threshold=min_score,
            )

        _render_eval_table(console, tool, result)

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
    import os
    import shutil
    import subprocess

    from rich.table import Table

    from .sources.discovery import discover_repo

    target_bin_dir = bin_dir or Path.home() / ".local" / "bin"
    if not target_bin_dir.exists():
        console.print(f"[red]Directory not found: {target_bin_dir}[/red]")
        raise typer.Exit(1)

    man_bin = shutil.which("man")
    if not man_bin:
        console.print(
            "[bold red]Error: 'man' utility is not installed or not in PATH.[/bold red]"
        )
        raise typer.Exit(1)

    try:
        table = Table(title=f"Executables in {target_bin_dir} Missing Manpages")
        table.add_column("Binary", style="cyan")
        table.add_column("Symlink Target", style="magenta")
        table.add_column("Discovered Repo", style="green")

        from rich.progress import track
        from .logging import logger

        missing_count = 0
        items = [i for i in sorted(target_bin_dir.iterdir()) if i.is_file() or i.is_symlink()]
        total_count = len(items)

        for item in track(items, description="Scanning executables..."):
            logger.debug(f"Checking {item.name}")
            res = subprocess.run(
                [man_bin, "-w", item.name],
                capture_output=True,
                text=True,
                check=False,
            )
            has_man = res.returncode == 0 and bool(res.stdout.strip())
            if not has_man:
                logger.debug(f"No manpage found for {item.name}, discovering repo...")
                missing_count += 1
                link_target = os.readlink(item) if item.is_symlink() else "direct"
                source = discover_repo(item.name, bin_dir=target_bin_dir)
                table.add_row(item.name, link_target, source.target)

        console.print(table)
        console.print(
            f"\n[bold]{missing_count}/{total_count}[/bold] binaries lack manpages."
        )
    except typer.Exit:
        raise
    except (OSError, RuntimeError, ManiacError) as e:
        console.print(f"[bold red]Error inspecting {target_bin_dir}: {e}[/bold red]")
        raise typer.Exit(1) from e


@app.command("list")
def list_cmd() -> None:
    """List all MANIAC-managed manpages with metadata and backup status."""
    from rich.table import Table

    from .installer import list_installed_manpages

    try:
        items = list_installed_manpages(default_cfg)
        if not items:
            console.print("[yellow]No MANIAC-managed manpages found.[/yellow]")
            return

        table = Table(title="MANIAC-Managed Manpages")
        table.add_column("Tool", style="cyan")
        table.add_column("Installed Path", style="magenta")
        table.add_column("Model", style="green")
        table.add_column("Generated Date", style="dim")
        table.add_column("Backup", justify="center")

        for item in items:
            bak_str = "[green]Yes[/green]" if item["has_backup"] else "-"
            table.add_row(
                item["tool"],
                str(item["path"]),
                item["model"],
                item["date"],
                bak_str,
            )

        console.print(table)
    except typer.Exit:
        raise
    except (OSError, RuntimeError, ManiacError) as e:
        console.print(f"[bold red]Error listing manpages: {e}[/bold red]")
        raise typer.Exit(1) from e


@app.command("uninstall")
def uninstall_cmd(
    tool: Annotated[str, typer.Argument(help="Tool name to uninstall.")],
    purge: Annotated[
        bool,
        typer.Option(
            "--purge",
            help="Also delete generated Markdown and intermediate context files.",
        ),
    ] = False,
) -> None:
    """Uninstall a MANIAC-generated manpage and restore vendor backup if present."""
    from .installer import uninstall_manpage

    try:
        result = uninstall_manpage(tool, purge=purge, config=default_cfg)
        if not result.removed and result.foreign_kept is None:
            console.print(f"[yellow]No installed manpage found for '{tool}'.[/yellow]")
            return

        if result.removed:
            console.print(f"[bold green]✓ Uninstalled manpage for {tool}![/bold green]")
            for p in result.removed:
                console.print(f" • Removed: {p}")

        if result.foreign_kept is not None:
            console.print(
                f"[yellow]⚠ Left non-MANIAC manpage in place at "
                f"{result.foreign_kept} (not ours to remove).[/yellow]"
            )
    except typer.Exit:
        raise
    except (OSError, RuntimeError, ManiacError) as e:
        console.print(
            f"[bold red]Error uninstalling manpage for {tool}: {e}[/bold red]"
        )
        raise typer.Exit(1) from e


if __name__ == "__main__":
    app()
