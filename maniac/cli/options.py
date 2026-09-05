"""Option type aliases shared across CLI commands, declared once."""

from typing import Annotated

import typer

OutputDirOption = Annotated[
    str, typer.Option(help="Directory to save generated manpage.")
]
CacheDirOption = Annotated[str, typer.Option(help="Cache directory for repositories.")]
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
DryRunOption = Annotated[bool, typer.Option("--dry-run", help="Skip LLM synthesis.")]
