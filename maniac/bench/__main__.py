"""CLI entry point for the benchmark harness: `python -m maniac.bench`."""

from typing import Annotated

import typer

from ..config import Config
from .harness import DEFAULT_MODELS, DEFAULT_TOOLS, run_benchmark

app = typer.Typer(
    help=(
        "Benchmark manpage generation across models and tools. "
        "Calls a real LLM for both generation and judging -- costs money per run."
    ),
)


@app.command()
def main(
    tool: Annotated[
        list[str] | None,
        typer.Option(
            help="CLI tool to benchmark. Repeatable; defaults to the built-in set."
        ),
    ] = None,
    model: Annotated[
        list[str] | None,
        typer.Option(
            help="Model alias to benchmark (resolved via Config.model_aliases). "
            "Repeatable; defaults to the built-in set."
        ),
    ] = None,
    retries: Annotated[
        int, typer.Option(help="Retry attempts per generation/evaluation step.")
    ] = 2,
) -> None:
    """Generate and judge a manpage for every (model, tool) combination."""
    cfg = Config()
    tools = tool or DEFAULT_TOOLS
    models = [(m, cfg.resolve_model(m)) for m in model] if model else DEFAULT_MODELS
    run_benchmark(tools=tools, models=models, config=cfg, retries=retries)


if __name__ == "__main__":
    app()
