"""Robust evaluation runner for benchmark results."""

import json
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from maniac.config import Config
from maniac.evaluation import evaluate_manpage
from maniac.exceptions import ManiacError

console = Console()

MODELS = [
    ("flash-high", "Gemini 3.7 Flash (High)"),
    ("flash-medium", "Gemini 3.7 Flash (Medium)"),
    ("flash-low", "Gemini 3.7 Flash (Low)"),
    ("flash-3.5-low", "Gemini 3.5 Flash (Low)"),
]

TOOLS = ["howdoi", "hx", "uv"]


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

    A run only touches the model/tool pairs in MODELS x TOOLS; historical
    rows for anything else are carried through unchanged so a later run
    cannot wipe out earlier results.
    """
    by_key = {(r["model_key"], r["tool"]): r for r in existing}
    for entry in new_entries:
        by_key[(entry["model_key"], entry["tool"])] = entry
    return list(by_key.values())


def evaluate_matrix(config: Config | None = None) -> None:
    cfg = config or Config()
    benchmark_dir = Path("data/benchmark")
    results_file = benchmark_dir / "results.json"

    if not results_file.exists():
        console.print(f"[red]Results file not found: {results_file}[/red]")
        return

    existing_results = json.loads(results_file.read_text(encoding="utf-8"))
    gen_times = {
        (r["model_key"], r["tool"]): r.get("gen_duration_sec", 0)
        for r in existing_results
    }

    final_results = []

    for model_key, model_name in MODELS:
        for tool in TOOLS:
            md_file = benchmark_dir / model_key / f"{tool}.1.md"
            context_file = cfg.intermediate_dir / f"{tool}_context.md"

            if not md_file.exists():
                console.print(f"[red]Missing file: {md_file}[/red]")
                final_results.append(
                    _skipped_entry(
                        model_key, model_name, tool, "missing generated file"
                    )
                )
                continue

            md_content = md_file.read_text(encoding="utf-8")
            context_content = context_file.read_text(encoding="utf-8")
            gen_duration = gen_times.get((model_key, tool), 0)

            console.print(
                f"[bold yellow]Evaluating {tool} ({model_key})...[/bold yellow]"
            )

            eval_res = None
            eval_duration = 0.0

            for attempt in range(3):
                eval_start = time.perf_counter()
                try:
                    eval_res = evaluate_manpage(
                        tool_name=tool,
                        manpage_text=md_content,
                        context_text=context_content,
                        model="Gemini 3.7 Flash (High)",
                    )
                    eval_end = time.perf_counter()
                    eval_duration = eval_end - eval_start
                    if eval_res and eval_res.score > 0:
                        break
                except (ManiacError, OSError, RuntimeError) as e:
                    console.print(f"[red]Attempt {attempt + 1} failed: {e}[/red]")
                    time.sleep(3)

            if not eval_res:
                console.print(
                    f"[red]Failed all attempts for {tool} ({model_key})[/red]"
                )
                final_results.append(
                    _skipped_entry(
                        model_key,
                        model_name,
                        tool,
                        "evaluation failed after 3 attempts",
                    )
                )
                continue

            entry = {
                "model_key": model_key,
                "model_name": model_name,
                "tool": tool,
                "status": "success",
                "gen_duration_sec": gen_duration,
                "eval_duration_sec": round(eval_duration, 2),
                "total_duration_sec": round(gen_duration + eval_duration, 2),
                "char_count": len(md_content),
                "line_count": len(md_content.splitlines()),
                "judge_score": eval_res.score,
                "judge_passed": eval_res.passed,
                "rubric": eval_res.rubric_breakdown,
                "defects": eval_res.defects,
                "summary": eval_res.summary,
            }
            final_results.append(entry)
            console.print(
                f"[bold green]✓ {tool} ({model_key}): Gen {gen_duration}s | Eval {round(eval_duration, 2)}s | Score: {eval_res.score}/100[/bold green]"
            )

    merged_results = _merge_results(existing_results, final_results)
    results_file.write_text(json.dumps(merged_results, indent=2), encoding="utf-8")

    table = Table(title="Complete Benchmark: Model Effort vs Performance & Speed")
    table.add_column("Model", style="cyan")
    table.add_column("Tool", style="magenta")
    table.add_column("Gen Time (s)", justify="right")
    table.add_column("Eval Time (s)", justify="right")
    table.add_column("Total Time (s)", justify="right")
    table.add_column("Lines", justify="right")
    table.add_column("Chars", justify="right")
    table.add_column("Score", justify="right", style="bold")
    table.add_column("Pass", justify="center")

    for r in final_results:
        if r["status"] == "skipped":
            table.add_row(
                r["model_key"],
                r["tool"],
                "-",
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
            str(r["char_count"]),
            f"{r['judge_score']}/100",
            "[green]✓[/green]" if r["judge_passed"] else "[red]✗[/red]",
        )

    console.print(table)


if __name__ == "__main__":
    evaluate_matrix()
