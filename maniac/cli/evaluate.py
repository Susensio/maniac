"""`eval`: score a generated manpage, or judge it head-to-head against the installed one.

`--against-installed` selects the second mode -- `compare` folded into `eval`
behind a flag per ADR-0013, rather than staying a separate command for the
same generated-manpage-plus-context inputs.

Each mode computes a result structure first and renders it after, so a
caller (or a test) can inspect the decision -- pass/fail, which error fired
-- without going through Rich-rendered prose.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer

from ..exceptions import ManiacError
from ..models import ComparisonResult, EvaluationResult
from . import app, console, default_cfg
from .options import ModelOption
from .render import _render_comparison, _render_eval_table


def _resolve_manpage_and_context(
    tool: str, manpage_file: Path | None, context_file: Path | None
) -> tuple[Path, Path]:
    manpage = (
        manpage_file
        if manpage_file is not None
        else default_cfg.output_dir / f"{tool}.1.md"
    )
    context = (
        context_file
        if context_file is not None
        else default_cfg.intermediate_dir / f"{tool}_context.md"
    )
    return manpage, context


@dataclass
class EvalOutcome:
    """Result of computing `eval`: a scored result, or the reason it could not run."""

    tool: str
    result: EvaluationResult | None = None
    error: str | None = None


def compute_eval(
    tool: str,
    manpage_file: Path | None = None,
    context_file: Path | None = None,
    model: str | None = None,
    min_score: int = 70,
) -> EvalOutcome:
    """Score a generated manpage against its extracted context. Never raises."""
    target_manpage, target_context = _resolve_manpage_and_context(
        tool, manpage_file, context_file
    )

    if not target_manpage.exists():
        return EvalOutcome(tool=tool, error=f"Manpage not found at {target_manpage}")
    if not target_context.exists():
        return EvalOutcome(
            tool=tool, error=f"Context file not found at {target_context}"
        )

    from ..evaluation.judge import evaluate_manpage

    try:
        result = evaluate_manpage(
            tool_name=tool,
            manpage_text=target_manpage.read_text(encoding="utf-8"),
            context_text=target_context.read_text(encoding="utf-8"),
            model=model,
            pass_threshold=min_score,
        )
    except (OSError, RuntimeError, ManiacError) as e:
        return EvalOutcome(tool=tool, error=str(e))
    return EvalOutcome(tool=tool, result=result)


def _render_eval_outcome(target_console: Any, outcome: EvalOutcome) -> None:
    if outcome.error is not None:
        target_console.print(f"[bold red]Error: {outcome.error}[/bold red]")
        return

    assert outcome.result is not None
    _render_eval_table(target_console, outcome.tool, outcome.result)
    if outcome.result.passed:
        target_console.print(
            f"\n[bold green]✓ Evaluation passed for {outcome.tool} "
            f"with score {outcome.result.score}/100![/bold green]"
        )
    else:
        target_console.print(
            f"\n[bold red]Evaluation failed for {outcome.tool} "
            f"(Score: {outcome.result.score}/100)[/bold red]"
        )


@dataclass
class CompareOutcome:
    """Result of computing `compare`: a head-to-head result, or the reason it could not run."""

    tool: str
    result: ComparisonResult | None = None
    installed_path: Path | None = None
    error: str | None = None


def compute_compare(
    tool: str,
    manpage_file: Path | None = None,
    context_file: Path | None = None,
    model: str | None = None,
    min_score: int = 70,
) -> CompareOutcome:
    """Judge a generated manpage against the one installed on this system. Never raises."""
    target_manpage, target_context = _resolve_manpage_and_context(
        tool, manpage_file, context_file
    )

    if not target_manpage.exists():
        return CompareOutcome(
            tool=tool,
            error=(
                f"Generated manpage not found at {target_manpage}. "
                f"Run 'maniac generate {tool}' first."
            ),
        )
    if not target_context.exists():
        return CompareOutcome(
            tool=tool, error=f"Context file not found at {target_context}"
        )

    from ..sources.manpages import find_installed_manpage_path, read_manpage_source

    installed_path = find_installed_manpage_path("man", tool)
    if installed_path is None:
        return CompareOutcome(
            tool=tool,
            error=(
                f"No manpage is currently installed for '{tool}' "
                f"(`man -w {tool}` found nothing)."
            ),
        )

    from ..evaluation.judge import compare_manpages

    try:
        result = compare_manpages(
            tool_name=tool,
            generated_text=target_manpage.read_text(encoding="utf-8"),
            installed_text=read_manpage_source(installed_path),
            context_text=target_context.read_text(encoding="utf-8"),
            model=model,
            pass_threshold=min_score,
        )
    except (OSError, RuntimeError, ManiacError) as e:
        return CompareOutcome(tool=tool, error=str(e))
    return CompareOutcome(tool=tool, result=result, installed_path=installed_path)


def _render_compare_outcome(target_console: Any, outcome: CompareOutcome) -> None:
    if outcome.error is not None:
        target_console.print(f"[bold red]Error: {outcome.error}[/bold red]")
        return

    assert outcome.result is not None
    assert outcome.installed_path is not None
    _render_comparison(
        target_console, outcome.tool, outcome.installed_path, outcome.result
    )


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
    against_installed: Annotated[
        bool,
        typer.Option(
            "--against-installed",
            help=(
                "Judge head-to-head against the manpage already installed on "
                "this system, instead of scoring against --min-score."
            ),
        ),
    ] = False,
) -> None:
    """Evaluate a generated manpage's quality, or compare it against the one installed here."""
    if against_installed:
        with console.status(f"[bold green]Comparing manpages for {tool}..."):
            compare_outcome = compute_compare(
                tool, manpage_file, context_file, model, min_score
            )

        _render_compare_outcome(console, compare_outcome)

        if compare_outcome.error is not None:
            raise typer.Exit(1)
        return

    with console.status(f"[bold green]Evaluating manpage for {tool}..."):
        outcome = compute_eval(tool, manpage_file, context_file, model, min_score)

    _render_eval_outcome(console, outcome)

    if outcome.error is not None or (
        outcome.result is not None and not outcome.result.passed
    ):
        raise typer.Exit(1)
