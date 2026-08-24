from typing import Annotated

import typer
from maniac.crawler import find_subcommands

app = typer.Typer(
    add_completion=False,
    help="Recursively scrape and extract CLI help manuals and subcommands.",
)


@app.command()
def main(
    cmd: Annotated[
        list[str],
        typer.Argument(help="Command and optional subcommands to crawl."),
    ],
) -> None:
    tree = find_subcommands(cmd)
    for header, body in tree.items():
        typer.echo(header)
        typer.echo(body)
        typer.echo()


if __name__ == "__main__":
    app()
