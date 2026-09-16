"""Option type aliases shared across CLI commands, declared once."""

from typing import Annotated

import typer

ModelOption = Annotated[
    str | None,
    typer.Option(help="LLM model ID (e.g. 'gemini/gemini-3.5-flash')."),
]
ForceOption = Annotated[
    bool,
    typer.Option(
        "--force",
        "-f",
        help="Force overwrite of foreign manpages with automatic backup.",
    ),
]
DryRunOption = Annotated[
    bool,
    typer.Option(
        "--dry-run", help="Preview without installing or generating anything."
    ),
]
