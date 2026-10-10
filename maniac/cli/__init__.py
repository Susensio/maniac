"""Maniac CLI entry point, split into one module per command group."""

from typing import Annotated, Any, cast

import typer

from ..config import Config

app = typer.Typer(
    help="Maniac: Scrape CLI help, extract repository docs, and synthesize elite Unix manpages.",
)


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
            help="Enable verbose debug logging.",
        ),
    ] = False,
) -> None:
    """Initialize CLI logging settings."""
    from ..logging import setup_logging

    ctx.obj = Config()
    setup_logging(verbose=verbose)


# Imported for side effects: each module registers its commands on `app`.
from . import evaluate, install, listing, pages, source, uninstall  # noqa: F401

# Re-exported for backward-compatible imports (tests, `python -m maniac.cli`).
from .render import _render_eval_table, _repo_cell  # noqa: F401

if __name__ == "__main__":
    app()
