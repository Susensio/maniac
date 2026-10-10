"""`maniac dev`: commands for working on maniac itself, left out of the main help.

Both call a real LLM and cost money per run. `eval` registers here from its
own module; the benchmark is defined below.
"""

from typing import Annotated

import typer

from . import app

dev = typer.Typer(
    help=(
        "Commands for working on maniac: judge a generated page, benchmark "
        "models. Each calls a real LLM and costs money per run."
    ),
    no_args_is_help=True,
)
app.add_typer(dev, name="dev", hidden=True)


@dev.command("bench")
def bench(
    tool: Annotated[
        list[str] | None,
        typer.Option(
            help="CLI tool to benchmark. Repeatable; defaults to the built-in set."
        ),
    ] = None,
    model: Annotated[
        list[str] | None,
        typer.Option(
            help="LiteLLM model identifier to benchmark, at the configured "
            "reasoning effort. Repeatable; defaults to the built-in set."
        ),
    ] = None,
    retries: Annotated[
        int, typer.Option(help="Retry attempts per generation/evaluation step.")
    ] = 2,
) -> None:
    """Generate and judge a page for every (model, tool) combination."""
    from ..bench.harness import DEFAULT_MODELS, DEFAULT_TOOLS, run_benchmark
    from ..config import Config

    cfg = Config()
    models = (
        [(m, cfg.resolve_model(m), cfg.resolve_reasoning_effort()) for m in model]
        if model
        else DEFAULT_MODELS
    )
    run_benchmark(
        tools=tool or DEFAULT_TOOLS, models=models, config=cfg, retries=retries
    )
