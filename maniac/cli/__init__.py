"""Maniac CLI entry point, split into one module per command group."""

from typing import Annotated, Any, cast

import typer

from ..config import Config

app = typer.Typer(
    help=(
        "Manpages for the tools you install, kept matching their versions: "
        "the page a tool ships, else its project's, else one an LLM writes "
        "from its --help."
    ),
)

# The order a user meets them: get pages, keep them current, take them out;
# then look at them, at the rest, and at why.
_COMMAND_ORDER = ("install", "update", "remove", "list", "scan", "why")


class _LazyConsole:
    _instance: Any = None

    def __getattr__(self, name: str) -> Any:
        if self._instance is None:
            from rich.console import Console

            self._instance = Console()
        return getattr(self._instance, name)


console = _LazyConsole()


def get_config(ctx: typer.Context) -> Config:
    """Return the configuration constructed for this CLI invocation."""
    return cast(Config, ctx.obj)


def require_login_path() -> None:
    """Stop once, up front, when the login shell cannot report `$PATH` (ADR-0062).

    Every binary resolves through it, so a broken one would otherwise fail
    each tool in turn with the same message; ADR-0060 says report it and exit.
    """
    from ..exceptions import BrokenLoginShell
    from ..sources.pathcache import path_dirs

    try:
        path_dirs()
    except BrokenLoginShell as e:
        console.print(f"[bold red]Cannot read $PATH from your {e}[/bold red]")
        raise typer.Exit(1) from e


@app.callback()
def main(
    ctx: typer.Context,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            envvar="MANIAC_VERBOSE",
            help="Also print diagnostic detail as maniac works.",
        ),
    ] = False,
) -> None:
    """Initialize CLI logging settings."""
    from ..logging import setup_logging

    ctx.obj = Config()
    setup_logging(verbose=verbose)


# Imported for side effects: each module registers its commands on `app`.
from . import dev, evaluate, install, listing, pages, remove, why  # noqa: F401


def _command_name(command: Any) -> str:
    return command.name or command.callback.__name__.replace("_", "-")


app.registered_commands.sort(
    key=lambda command: (
        _COMMAND_ORDER.index(_command_name(command))
        if _command_name(command) in _COMMAND_ORDER
        else len(_COMMAND_ORDER)
    )
)

# Re-exported for backward-compatible imports (tests, `python -m maniac.cli`).
from .render import _render_eval_table, _repo_cell  # noqa: F401

if __name__ == "__main__":
    app()
