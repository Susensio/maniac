"""`generate-missing`, `list-missing`, `list`, `uninstall`: bulk scan and inventory commands."""

import os
import shutil
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from ..exceptions import CrawlerError, ManiacError
from ..sources.manpages import inspect_manpage
from . import app, console, default_cfg
from .options import CacheDirOption, ForceOption, ModelOption, OutputDirOption
from .render import _repo_cell

CANDIDATE_SUBCOMMAND_TIMEOUT_SECONDS = 1


@app.command("generate-missing")
def generate_missing(
    bin_dir: Annotated[Path | None, typer.Option(help="Directory to inspect.")] = None,
    output_dir: OutputDirOption = str(default_cfg.output_dir),
    cache_dir: CacheDirOption = str(default_cfg.cache_dir),
    model: ModelOption = None,
    install: Annotated[
        bool, typer.Option(help="Install compiled manpage to ~/.local/share/man/man1.")
    ] = True,
    force: ForceOption = False,
) -> None:
    """Find all executables lacking manpages and generate them automatically."""
    from ..orchestration.pipeline import run_pipeline

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

    items = [
        item
        for item in sorted(target_bin_dir.iterdir())
        if item.is_file() and os.access(item, os.X_OK)
    ]
    missing_tools: list[tuple[str, bool]] = []

    with console.status(
        f"[bold cyan]Scanning {target_bin_dir} for missing manpages..."
    ):
        for item in items:
            status = inspect_manpage(man_bin, item.name)
            if not status.is_usable:
                missing_tools.append((item.name, status.is_help2man))

    if not missing_tools:
        console.print(
            "[bold green]All binaries have manpages. Nothing to do![/bold green]"
        )
        return

    console.print(
        f"[bold yellow]Found {len(missing_tools)} binaries missing manpages.[/bold yellow]"
    )

    failures = 0
    for tool, replace_help2man in missing_tools:
        console.print(f"\n[bold blue]=== Generating for {tool} ===[/bold blue]")
        try:
            with console.status(f"[bold green]Generating manpage for {tool}..."):
                run_pipeline(
                    tool_name=tool,
                    bin_dir=target_bin_dir,
                    cache_dir=cache_dir,
                    output_dir=output_dir,
                    prompt_file=None,
                    model=model,
                    install=install,
                    force=force or replace_help2man,
                    dry_run=False,
                )
            console.print(
                f"[bold green]✓ Successfully generated manpage for {tool}![/bold green]"
            )
        except (OSError, RuntimeError, ManiacError) as e:
            console.print(f"[bold red]Failed {tool}: {e}[/bold red]")
            failures += 1

    if failures:
        console.print(
            f"\n[bold red]{failures}/{len(missing_tools)} tool(s) failed to generate.[/bold red]"
        )
        raise typer.Exit(1)


@app.command("list-missing")
def list_missing(
    bin_dir: Annotated[Path | None, typer.Option(help="Directory to inspect.")] = None,
    include_candidates: Annotated[
        bool,
        typer.Option(
            "--include-candidates",
            help="Also scan the active manpath for manpages that MANIAC can improve.",
        ),
    ] = False,
) -> None:
    """List executables that lack manpages or can be improved."""
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

    if include_candidates:
        _list_missing_with_candidates(target_bin_dir, man_bin)
        return

    try:
        from ..sources.discovery import discover_repo

        table = Table(title=f"Executables in {target_bin_dir} Missing Manpages")
        table.add_column("Binary", style="cyan")
        table.add_column("Symlink Target", style="magenta")
        table.add_column("Discovered Repo", style="green")

        from rich.progress import track

        from ..logging import logger

        missing_count = 0
        items = [
            i for i in sorted(target_bin_dir.iterdir()) if i.is_file() or i.is_symlink()
        ]
        total_count = len(items)

        for item in track(items, description="Scanning executables..."):
            logger.debug(f"Checking {item.name}")
            if not inspect_manpage(man_bin, item.name).is_usable:
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


def _list_missing_with_candidates(target_bin_dir: Path, man_bin: str) -> None:
    """`list-missing --include-candidates`: also scan the active manpath for improvable pages."""
    manpath_bin = shutil.which("manpath")
    if not manpath_bin:
        console.print(
            "[bold red]Error: 'manpath' utility is not installed or not in PATH.[/bold red]"
        )
        raise typer.Exit(1)

    try:
        from rich.progress import Progress

        from ..models import RepoSource
        from ..sources.crawler import extract_subcommands, get_help
        from ..sources.discovery import discover_candidate_source, discover_repo
        from ..sources.manpages import HelpDerivedManpage, find_help_derived_manpages

        table = Table(title=f"Manpage Candidates for {target_bin_dir}")
        table.add_column("Candidate", style="cyan")
        table.add_column("Status", style="yellow")
        table.add_column("Path", style="magenta", overflow="fold")
        table.add_column("Discovered Source", style="green", overflow="fold")

        items = [
            i
            for i in sorted(target_bin_dir.iterdir())
            if i.is_file() and os.access(i, os.X_OK)
        ]
        missing_items: list[Path] = []
        with Progress() as progress:
            binaries_task = progress.add_task(
                "Scanning executables...", total=len(items)
            )
            for item in items:
                if not inspect_manpage(man_bin, item.name).exists:
                    missing_items.append(item)
                progress.advance(binaries_task)

            manpages_task = progress.add_task("Scanning active manpath...", total=None)
            help_derived_pages = find_help_derived_manpages(
                manpath_bin,
                on_scan=lambda: progress.advance(manpages_task),
                on_start=lambda total: progress.update(manpages_task, total=total),
            )
            candidate_sources_task = progress.add_task(
                "Resolving candidate sources...", total=len(help_derived_pages)
            )
            source_backed_pages: list[tuple[HelpDerivedManpage, RepoSource]] = []
            for page in help_derived_pages:
                source = discover_candidate_source(page.name)
                if source is not None:
                    source_backed_pages.append((page, source))
                progress.advance(candidate_sources_task)
            source_backed_names = {page.name for page, _ in source_backed_pages}
            subcommand_pages: list[tuple[HelpDerivedManpage, int]] = []
            unbacked_pages = [
                page
                for page in help_derived_pages
                if page.name not in source_backed_names
            ]
            subcommands_task = progress.add_task(
                "Checking candidate subcommands...", total=len(unbacked_pages)
            )
            for page in unbacked_pages:
                executable = shutil.which(page.name)
                if executable is None:
                    progress.advance(subcommands_task)
                    continue
                try:
                    help_text = get_help(
                        [executable],
                        timeout=CANDIDATE_SUBCOMMAND_TIMEOUT_SECONDS,
                    )
                except CrawlerError:
                    progress.advance(subcommands_task)
                    continue
                subcommands = extract_subcommands(help_text, cmd_name=page.name)
                if subcommands:
                    subcommand_pages.append((page, len(subcommands)))
                progress.advance(subcommands_task)
            repositories_task = progress.add_task(
                "Discovering repositories...",
                total=len(missing_items),
            )
            for item in missing_items:
                table.add_row(
                    item.name,
                    "Missing",
                    str(item),
                    _repo_cell(discover_repo(item.name, bin_dir=target_bin_dir)),
                )
                progress.advance(repositories_task)
            for page, source in source_backed_pages:
                table.add_row(
                    f"{page.name}({page.section})",
                    "Help-derived",
                    str(page.path),
                    _repo_cell(source),
                )
            for page, count in subcommand_pages:
                table.add_row(
                    f"{page.name}({page.section})",
                    f"Subcommands ({count})",
                    str(page.path),
                    _repo_cell(
                        RepoSource(
                            name=page.name,
                            target=page.name,
                            is_local=False,
                        )
                    ),
                )

        console.print(table)
        skipped_pages = (
            len(help_derived_pages) - len(source_backed_pages) - len(subcommand_pages)
        )
        console.print(
            f"\n[bold]{len(missing_items)}[/bold] missing and "
            f"[bold]{len(source_backed_pages)}[/bold] source-backed help-derived "
            f"manpage(s); [bold]{len(subcommand_pages)}[/bold] subcommand-backed "
            f"help-derived manpage(s); [bold]{skipped_pages}[/bold] "
            "skipped because their source is unknown."
        )
    except typer.Exit:
        raise
    except (OSError, RuntimeError, ManiacError) as e:
        console.print(f"[bold red]Error inspecting {target_bin_dir}: {e}[/bold red]")
        raise typer.Exit(1) from e


@app.command("list")
def list_cmd() -> None:
    """List all MANIAC-managed manpages with metadata and backup status."""
    from ..installer import list_installed_manpages

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
    from ..installer import uninstall_manpage

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
