"""Rendering helpers: turn a result object into Rich console output."""

from pathlib import Path
from typing import Any


def _repo_cell(source: Any, *, blank_when_unresolvable: bool = False) -> Any:
    """Render a discovered repository as a terminal link or an explicit fallback.

    `blank_when_unresolvable` swaps the "Unknown" placeholder for an empty
    cell (ADR-0018's `list`: blank means nothing was resolvable, the same
    explanation a `missing` row's Source column carries). The eval table's
    own caller omits the flag and keeps "Unknown" unchanged.
    """
    from rich.style import Style
    from rich.text import Text

    if not source.is_local and source.target == source.name:
        return Text("" if blank_when_unresolvable else "Unknown", style="dim")

    clone_url = source.clone_url
    if clone_url is None:
        return Text(source.target, style="yellow")
    return Text(
        source.target,
        style=Style(
            color="green",
            link=clone_url.removesuffix(".git"),
        ),
    )


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


def _render_comparison(
    target_console: Any, tool: str, installed_path: Path, result: Any
) -> None:
    """Render and display a head-to-head installed-vs-generated manpage comparison."""
    from rich.table import Table

    table = Table(title=f"Manpage Comparison: {tool}")
    table.add_column("", style="cyan")
    table.add_column("Installed", justify="right")
    table.add_column("MANIAC-generated", justify="right")

    table.add_row("Source", str(installed_path), "(generated)")
    table.add_row(
        "Score", f"{result.installed.score}/100", f"{result.generated.score}/100"
    )
    table.add_row(
        "Passed",
        "✓" if result.installed.passed else "✗",
        "✓" if result.generated.passed else "✗",
    )

    target_console.print(table)

    winner_label = {
        "installed": "[bold]Installed[/bold] manpage judged better overall.",
        "generated": "[bold]MANIAC-generated[/bold] manpage judged better overall.",
        "tie": "The two manpages are judged roughly equivalent overall.",
    }[result.winner]
    target_console.print(f"\n{winner_label}")

    if result.differences:
        target_console.print(f"\n[bold]Differences:[/bold] {result.differences}")

    if result.installed_strengths:
        target_console.print("\n[bold cyan]Installed page strengths:[/bold cyan]")
        for s in result.installed_strengths:
            target_console.print(f" • [cyan]{s}[/cyan]")

    if result.generated_strengths:
        target_console.print("\n[bold green]Generated page strengths:[/bold green]")
        for s in result.generated_strengths:
            target_console.print(f" • [green]{s}[/green]")
