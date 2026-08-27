"""Benchmark script to measure model effort impact on speed and judge quality."""

import json
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from maniac.config import Config
from maniac.evaluation import evaluate_manpage
from maniac.exceptions import ManiacError
from maniac.logging import logger
from maniac.orchestration import run_pipeline

console = Console()

MODELS = [
    ("flash-high", "Gemini 3.7 Flash (High)"),
    ("flash-medium", "Gemini 3.7 Flash (Medium)"),
    ("flash-low", "Gemini 3.7 Flash (Low)"),
    ("flash-3.5-low", "Gemini 3.5 Flash (Low)"),
]

TOOLS = ["howdoi", "hx", "uv"]


def run_benchmark(config: Config | None = None) -> None:
    cfg = config or Config()
    benchmark_dir = Path("data/benchmark")
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    results_file = benchmark_dir / "results.json"

    all_results: list[dict[str, Any]] = []

    console.print("[bold blue]Starting Benchmark across Models and Tools[/bold blue]")

    for model_key, model_name in MODELS:
        for tool in TOOLS:
            console.print(
                f"\n[bold yellow]>>> Benchmarking {tool} with {model_key} ({model_name})...[/bold yellow]"
            )
            out_dir = benchmark_dir / model_key
            out_dir.mkdir(parents=True, exist_ok=True)

            # Measure Generation
            gen_start = time.perf_counter()
            retries = 2
            pipeline_res = None
            gen_error = None

            for attempt in range(retries + 1):
                try:
                    pipeline_res = run_pipeline(
                        tool_name=tool,
                        cache_dir=cfg.cache_dir,
                        output_dir=out_dir,
                        intermediate_dir=cfg.intermediate_dir,
                        model=model_name,
                        install=False,
                        dry_run=False,
                    )
                    break
                except (ManiacError, OSError, RuntimeError) as e:
                    gen_error = str(e)
                    logger.warning(
                        "Generation attempt failed",
                        attempt=attempt + 1,
                        tool=tool,
                        model=model_key,
                        error=str(e),
                    )
                    if attempt < retries:
                        time.sleep(2)

            gen_end = time.perf_counter()
            gen_duration = gen_end - gen_start

            if not pipeline_res or not pipeline_res.markdown_path.exists():
                console.print(
                    f"[bold red]FAILED generation for {tool} with {model_key}: {gen_error}[/bold red]"
                )
                all_results.append(
                    {
                        "model_key": model_key,
                        "model_name": model_name,
                        "tool": tool,
                        "status": "failed_generation",
                        "error": gen_error,
                        "gen_duration": round(gen_duration, 2),
                    }
                )
                continue

            md_content = pipeline_res.markdown_path.read_text(encoding="utf-8")
            assert pipeline_res.context_path is not None, (
                "run_pipeline succeeded (dry_run=False) but returned no context_path"
            )
            context_content = pipeline_res.context_path.read_text(encoding="utf-8")

            # Measure Evaluation with Standard Judge (Gemini 3.7 Flash High)
            eval_start = time.perf_counter()
            eval_res = None
            eval_error = None

            for attempt in range(retries + 1):
                try:
                    eval_res = evaluate_manpage(
                        tool_name=tool,
                        manpage_text=md_content,
                        context_text=context_content,
                        model="Gemini 3.7 Flash (High)",
                    )
                    break
                except (ManiacError, OSError, RuntimeError) as e:
                    eval_error = str(e)
                    logger.warning(
                        "Evaluation attempt failed",
                        attempt=attempt + 1,
                        tool=tool,
                        model=model_key,
                        error=str(e),
                    )
                    if attempt < retries:
                        time.sleep(2)

            eval_end = time.perf_counter()
            eval_duration = eval_end - eval_start

            if not eval_res:
                console.print(
                    f"[bold red]FAILED eval for {tool} with {model_key}: {eval_error}[/bold red]"
                )
                all_results.append(
                    {
                        "model_key": model_key,
                        "model_name": model_name,
                        "tool": tool,
                        "status": "failed_eval",
                        "error": eval_error,
                        "gen_duration": round(gen_duration, 2),
                        "eval_duration": round(eval_duration, 2),
                    }
                )
                continue

            entry = {
                "model_key": model_key,
                "model_name": model_name,
                "tool": tool,
                "status": "success",
                "gen_duration_sec": round(gen_duration, 2),
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
            all_results.append(entry)

            console.print(
                f"[bold green]✓ {tool} ({model_key}): Gen {entry['gen_duration_sec']}s | Eval {entry['eval_duration_sec']}s | Score: {entry['judge_score']}/100[/bold green]"
            )

    # Save results
    results_file.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    console.print(
        f"\n[bold green]Benchmark complete! Results saved to {results_file}[/bold green]"
    )

    # Print summary table
    table = Table(title="Benchmark Summary: Model Effort vs Performance and Speed")
    table.add_column("Model", style="cyan")
    table.add_column("Tool", style="magenta")
    table.add_column("Gen Time (s)", justify="right")
    table.add_column("Eval Time (s)", justify="right")
    table.add_column("Total Time (s)", justify="right")
    table.add_column("Lines", justify="right")
    table.add_column("Score", justify="right", style="bold")
    table.add_column("Pass", justify="center")

    for r in all_results:
        if r.get("status") == "success":
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
        else:
            table.add_row(
                r["model_key"],
                r["tool"],
                f"{r.get('gen_duration', 0):.1f}",
                "-",
                "-",
                "-",
                "ERR",
                "[red]✗[/red]",
            )

    console.print(table)


if __name__ == "__main__":
    run_benchmark()
