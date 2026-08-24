import pytest
from typer.testing import CliRunner

from maniac.cli import app

runner = CliRunner()


def test_cli_no_args() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code != 0
    assert "Missing argument" in result.output or "Usage:" in result.output


def test_cli_with_args(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "maniac.cli.find_subcommands",
        lambda cmd: {"> git --help": "git help content"},
    )
    result = runner.invoke(app, ["git"])
    assert result.exit_code == 0
    assert "> git --help" in result.output
    assert "git help content" in result.output
