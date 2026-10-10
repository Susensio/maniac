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
            "Install where maniac would refuse: over a page it did not "
            "install (kept as a backup), for a tool installed only in this "
            "project, or for a system package's tool. Also reinstalls a page "
            "already current."
        ),
    ),
]
DryRunOption = Annotated[
    bool,
    typer.Option(
        "--dry-run", help="Preview without installing or generating anything."
    ),
]
NamesOption = Annotated[
    bool,
    typer.Option("--names", help="Bare tool names, one per line, even on a terminal."),
]
OutdatedOption = Annotated[
    bool,
    typer.Option("--outdated", help="Only pages that document another version."),
]
UnknownOption = Annotated[
    bool,
    typer.Option(
        "--unknown", help="Only pages whose version nothing proves either way."
    ),
]
