"""`why <tool>` against real binaries, installers and `man` (CONTRACT.md rule 3)."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from maniac.cli import app
from maniac.config import Config

runner = CliRunner()


def _flat(output: str) -> str:
    """`output` with Rich's wrapping undone, for substring checks."""
    return " ".join(output.split())


@pytest.fixture
def canned_model(monkeypatch: pytest.MonkeyPatch, model_response: str) -> None:
    monkeypatch.setattr(
        "maniac.generation.llm._complete_litellm",
        lambda request: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=model_response))]
        ),
    )
    monkeypatch.setattr("maniac.generation.llm._supports_reasoning", lambda m: False)


def test_why_explains_a_wrapper_before_and_after_its_page_is_installed(
    faketool: Path,
    canned_model: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool no installer claims, with a venv copy in this shell: each
    decision is stated, and so is the page once maniac installs one."""
    from maniac.sources import pathcache

    venv_bin = tmp_path / "project" / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "faketool").write_text(
        faketool.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (venv_bin / "faketool").chmod(0o755)
    login_path = os.environ["PATH"]
    monkeypatch.setattr(pathcache, "_spawn_login_shell", lambda: login_path)
    monkeypatch.setenv("PATH", f"{venv_bin}{os.pathsep}{login_path}")
    monkeypatch.setenv("VIRTUAL_ENV", str(venv_bin.parent))
    monkeypatch.setenv("MANPATH", str(Config().man_dir.parent))

    before = runner.invoke(app, ["why", "faketool"])

    assert before.exit_code == 0, before.output
    out = _flat(before.output)
    assert (
        f"left out: {venv_bin}, an activated virtualenv (VIRTUAL_ENV) (holds a copy of this tool)"
        in out
    )
    assert f"login $PATH reaches {faketool}" in out
    assert "no installer claims it; it reports faketool 2.3.1" in out
    assert f"this shell runs {venv_bin / 'faketool'} instead" in out
    assert "`man` finds no page for it" in out
    assert "shipped: no installer, so no install root to look in" in out
    assert "install generated" in out

    installed = runner.invoke(
        app, ["install", "--model", "openai/gpt-4o-mini", "faketool"]
    )
    assert installed.exit_code == 0, installed.output
    after = _flat(runner.invoke(app, ["why", "faketool"]).output)

    assert (
        "maniac's (generated): documents faketool 2.3.1, installed faketool 2.3.1: ok"
        in after
    )
    # What `install` would do now: leave it alone, as it does.
    assert (
        "install already up to date (faketool 2.3.1); --force reinstalls it (generated)"
        in after
    )
    again = runner.invoke(app, ["install", "faketool"])
    assert "already up to date (faketool 2.3.1)" in _flat(again.output)


def test_why_finds_the_page_npm_shipped(
    npm_prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from maniac.sources import pathcache

    login_path = f"{npm_prefix / 'bin'}{os.pathsep}{os.environ['PATH']}"
    monkeypatch.setattr(pathcache, "_spawn_login_shell", lambda: login_path)
    monkeypatch.setenv("PATH", login_path)

    result = runner.invoke(app, ["why", "marked"])

    assert result.exit_code == 0, result.output
    out = _flat(result.output)
    assert "installed by npm as marked (16.4.2)" in out
    assert f"shipped: {npm_prefix / 'lib/node_modules/marked/man/marked.1'}" in out
    assert "install shipped" in out


def test_why_says_why_a_tool_nowhere_on_path_is_refused() -> None:
    result = runner.invoke(app, ["why", "no-such-tool-anywhere"])

    out = _flat(result.output)
    assert result.exit_code == 0, result.output
    assert "not on the login $PATH" in out
    assert "none tried: not installed globally" in out
    assert "install refused: not installed globally" in out
