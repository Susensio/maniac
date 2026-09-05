"""`eval` and `compare`: score a generated manpage, or judge it against the installed one."""

from pathlib import Path
from typing import Annotated

import typer

from ..exceptions import ManiacError
from . import app, console, default_cfg
from .options import ModelOption
from .render import _render_comparison, _render_eval_table


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
    model: ModelOption = None,
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

    from ..evaluation.judge import evaluate_manpage

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


@app.command("compare")
def compare_cmd(
    tool: Annotated[str, typer.Argument(help="Name of the tool to compare.")],
    manpage_file: Annotated[
        Path | None,
        typer.Option(
            help="Path to MANIAC's generated manpage Markdown file (default: $XDG_DATA_HOME/maniac/manpages/<tool>.1.md)."
        ),
    ] = None,
    context_file: Annotated[
        Path | None,
        typer.Option(
            help="Path to context file (default: $XDG_STATE_HOME/maniac/intermediate/<tool>_context.md)."
        ),
    ] = None,
    model: ModelOption = None,
    min_score: Annotated[
        int,
        typer.Option(help="Minimum passing score threshold (0-100)."),
    ] = 70,
) -> None:
    """Compare the manpage already installed on this system against the one MANIAC would generate."""
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
            f"[bold red]Error: Generated manpage not found at {target_manpage}. "
            f"Run 'maniac generate {tool}' first.[/bold red]"
        )
        raise typer.Exit(1)

    if not target_context.exists():
        console.print(
            f"[bold red]Error: Context file not found at {target_context}[/bold red]"
        )
        raise typer.Exit(1)

    from ..sources.manpages import find_installed_manpage_path, read_manpage_source

    installed_path = find_installed_manpage_path("man", tool)
    if installed_path is None:
        console.print(
            f"[bold red]Error: No manpage is currently installed for '{tool}' "
            f"(`man -w {tool}` found nothing).[/bold red]"
        )
        raise typer.Exit(1)

    generated_text = target_manpage.read_text(encoding="utf-8")
    context_text = target_context.read_text(encoding="utf-8")

    from ..evaluation.judge import compare_manpages

    try:
        installed_text = read_manpage_source(installed_path)

        with console.status(f"[bold green]Comparing manpages for {tool}..."):
            result = compare_manpages(
                tool_name=tool,
                generated_text=generated_text,
                installed_text=installed_text,
                context_text=context_text,
                model=model,
                pass_threshold=min_score,
            )

        _render_comparison(console, tool, installed_path, result)

    except typer.Exit:
        raise
    except (OSError, RuntimeError, ManiacError) as e:
        console.print(f"[bold red]Comparison error for {tool}: {e}[/bold red]")
        raise typer.Exit(1) from e
