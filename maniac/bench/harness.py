"""Model x tool benchmark harness: generate manpages, judge them, and track results.

Runs to `bench_dir` merge into any prior `results.json` instead of overwriting
it, and a combo that fails generation or evaluation is recorded as a skipped
row rather than dropped silently -- both were audit finding M8, and
`tests/test_bench_harness.py` guards the regression.
"""

import dataclasses
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from ..config import Config
from ..evaluation import evaluate_manpage
from ..exceptions import ManiacError
from ..logging import logger
from ..models import EvaluationResult, PipelineResult
from ..orchestration import resolve_tool
from ..orchestration.pipeline import synthesize

console = Console()

RETRYABLE_ERRORS = (ManiacError, OSError, RuntimeError)

# (key, LiteLLM model identifier, reasoning effort) -- bench varies both model
# and effort for the same underlying model, so effort is tracked alongside it.
DEFAULT_MODELS: list[tuple[str, str, str | None]] = [
    ("gemini-flash-high", "gemini/gemini-flash-latest", "high"),
    ("gemini-flash-medium", "gemini/gemini-flash-latest", "medium"),
    ("gemini-flash-low", "gemini/gemini-flash-latest", "low"),
    ("claude-sonnet", "anthropic/claude-sonnet-4-6", None),
]

DEFAULT_TOOLS: list[str] = ["howdoi", "hx", "uv"]

JUDGE_MODEL = "gemini/gemini-flash-latest"


def _skipped_entry(
    model_key: str, model_name: str, tool: str, reason: str
) -> dict[str, Any]:
    return {
        "model_key": model_key,
        "model_name": model_name,
        "tool": tool,
        "status": "skipped",
        "reason": reason,
    }


def _merge_results(
    existing: list[dict[str, Any]], new_entries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge this run's entries into prior results, keyed by (model_key, tool).

    A run only touches the model/tool pairs it was given; historical rows for
    anything else are carried through unchanged so a later run cannot wipe
    out earlier results.
    """
    by_key = {(r["model_key"], r["tool"]): r for r in existing}
    for entry in new_entries:
        by_key[(entry["model_key"], entry["tool"])] = entry
    return list(by_key.values())


def _load_results(results_file: Path) -> list[dict[str, Any]]:
    if not results_file.exists():
        return []
    return json.loads(results_file.read_text(encoding="utf-8"))


def _generate(
    tool: str,
    model_name: str,
    reasoning_effort: str | None,
    cfg: Config,
    out_dir: Path,
    retries: int,
) -> tuple[PipelineResult | None, str | None, float]:
    start = time.perf_counter()
    error: str | None = None
    for attempt in range(retries + 1):
        try:
            result = synthesize(
                resolve_tool(tool, config=dataclasses.replace(cfg, output_dir=out_dir)),
                model=model_name,
                reasoning_effort=reasoning_effort,
                install=False,
                dry_run=False,
            )
            return result, None, time.perf_counter() - start
        except RETRYABLE_ERRORS as e:
            error = str(e)
            logger.warning(
                "Generation attempt failed", attempt=attempt + 1, tool=tool, error=error
            )
            if attempt < retries:
                time.sleep(2)
    return None, error, time.perf_counter() - start


def _evaluate(
    tool: str,
    manpage_text: str,
    context_text: str,
    retries: int,
) -> tuple[EvaluationResult | None, str | None, float]:
    start = time.perf_counter()
    error: str | None = None
    for attempt in range(retries + 1):
        try:
            result = evaluate_manpage(
                tool_name=tool,
                manpage_text=manpage_text,
                context_text=context_text,
                model=JUDGE_MODEL,
            )
            return result, None, time.perf_counter() - start
        except RETRYABLE_ERRORS as e:
            error = str(e)
            logger.warning(
                "Evaluation attempt failed", attempt=attempt + 1, tool=tool, error=error
            )
            if attempt < retries:
                time.sleep(2)
    return None, error, time.perf_counter() - start


def _print_table(entries: list[dict[str, Any]]) -> None:
    table = Table(title="Benchmark: Model Effort vs Performance and Speed")
    table.add_column("Model", style="cyan")
    table.add_column("Tool", style="magenta")
    table.add_column("Gen Time (s)", justify="right")
    table.add_column("Eval Time (s)", justify="right")
    table.add_column("Total Time (s)", justify="right")
    table.add_column("Lines", justify="right")
    table.add_column("Score", justify="right", style="bold")
    table.add_column("Pass", justify="center")

    for r in entries:
        if r["status"] == "skipped":
            table.add_row(
                r["model_key"],
                r["tool"],
                "-",
                "-",
                "-",
                "-",
                "-",
                f"[yellow]skipped: {r['reason']}[/yellow]",
            )
            continue
        table.add_row(
            r["model_key"],
            r["tool"],
            f"{r['gen_duration_sec']:.1f}",
            f"{r['eval_duration_sec']:.1f}",
            f"{r['total_duration_sec']:.1f}",
            str(r["line_count"]),
            f"{r['judge_score']}/100",
            "[green]✓[/green]" if r["judge_passed"] else "[red]✗[/red]",
        )

    console.print(table)


def run_benchmark(
    tools: list[str] | None = None,
    models: Sequence[tuple[str, str, str | None]] | None = None,
    config: Config | None = None,
    retries: int = 2,
) -> list[dict[str, Any]]:
    """Generate and judge a manpage for every (model, tool) combination.

    Merges into any prior `results.json` under `config.bench_dir` rather than
    overwriting it; a combo whose generation or evaluation fails after
    retries is recorded with status "skipped" and a reason instead of being
    dropped.
    """
    cfg = config or Config()
    tools = tools if tools is not None else DEFAULT_TOOLS
    models = models if models is not None else DEFAULT_MODELS

    bench_dir = cfg.bench_dir
    bench_dir.mkdir(parents=True, exist_ok=True)
    results_file = bench_dir / "results.json"
    existing_results = _load_results(results_file)

    new_entries: list[dict[str, Any]] = []

    console.print("[bold blue]Starting Benchmark across Models and Tools[/bold blue]")

    for model_key, model_name, reasoning_effort in models:
        for tool in tools:
            console.print(
                f"\n[bold yellow]>>> Benchmarking {tool} with {model_key} "
                f"({model_name}, effort={reasoning_effort})...[/bold yellow]"
            )
            out_dir = bench_dir / model_key
            out_dir.mkdir(parents=True, exist_ok=True)

            pipeline_res, gen_error, gen_duration = _generate(
                tool, model_name, reasoning_effort, cfg, out_dir, retries
            )
            if pipeline_res is None:
                console.print(
                    f"[bold red]FAILED generation for {tool} with "
                    f"{model_key}: {gen_error}[/bold red]"
                )
                new_entries.append(
                    _skipped_entry(
                        model_key, model_name, tool, f"generation failed: {gen_error}"
                    )
                )
                continue

            assert pipeline_res.context_path is not None, (
                "synthesis succeeded (dry_run=False) but returned no context_path"
            )
            context_content = pipeline_res.context_path.read_text(encoding="utf-8")

            eval_res, eval_error, eval_duration = _evaluate(
                tool, pipeline_res.markdown_content, context_content, retries
            )
            if eval_res is None:
                console.print(
                    f"[bold red]FAILED eval for {tool} with "
                    f"{model_key}: {eval_error}[/bold red]"
                )
                new_entries.append(
                    _skipped_entry(
                        model_key, model_name, tool, f"evaluation failed: {eval_error}"
                    )
                )
                continue

            entry = {
                "model_key": model_key,
                "model_name": model_name,
                "reasoning_effort": reasoning_effort,
                "tool": tool,
                "status": "success",
                "gen_duration_sec": round(gen_duration, 2),
                "eval_duration_sec": round(eval_duration, 2),
                "total_duration_sec": round(gen_duration + eval_duration, 2),
                "char_count": len(pipeline_res.markdown_content),
                "line_count": len(pipeline_res.markdown_content.splitlines()),
                "judge_score": eval_res.score,
                "judge_passed": eval_res.passed,
                "rubric": eval_res.rubric_breakdown,
                "defects": eval_res.defects,
                "summary": eval_res.summary,
            }
            new_entries.append(entry)

            console.print(
                f"[bold green]✓ {tool} ({model_key}): "
                f"Gen {entry['gen_duration_sec']}s | "
                f"Eval {entry['eval_duration_sec']}s | "
                f"Score: {entry['judge_score']}/100[/bold green]"
            )

    merged_results = _merge_results(existing_results, new_entries)
    results_file.write_text(json.dumps(merged_results, indent=2), encoding="utf-8")
    console.print(
        f"\n[bold green]Benchmark complete! Results saved to "
        f"{results_file}[/bold green]"
    )

    _print_table(new_entries)
    return merged_results
