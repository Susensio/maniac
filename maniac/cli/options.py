"""Option type aliases shared across CLI commands, declared once."""

from typing import Annotated

import typer

ModelOption = Annotated[
    str | None,
    typer.Option(help="LLM model ID (e.g. 'gemini/gemini-3.5-flash')."),
]
NoGenerateOption = Annotated[
    bool,
    typer.Option(
        "--no-generate",
        help="Use only a shipped or upstream page; never call an LLM.",
    ),
]
ForceOption = Annotated[
    bool,
    typer.Option(
        "--force",
        "-f",
        help=(
            "Install where maniac would refuse: replace a page it did not "
            "install (kept as a backup), or document a tool installed only "
            "for this project or a system package's tool."
        ),
    ),
]
DryRunOption = Annotated[
    bool,
    typer.Option(
        "--dry-run", help="Preview without installing or generating anything."
    ),
]
