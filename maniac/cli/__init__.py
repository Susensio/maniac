"""Maniac CLI entry point, split into one module per command group."""

from typing import Annotated, Any

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
default_cfg = Config()


@app.callback()
def main(
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

    setup_logging(verbose=verbose)


# Imported for side effects: each module registers its commands on `app`.
from . import evaluate, install, source, status, uninstall  # noqa: F401

# Re-exported for backward-compatible imports (tests, `python -m maniac.cli`).
from .render import _render_eval_table, _repo_cell  # noqa: F401

if __name__ == "__main__":
    app()
