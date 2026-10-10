"""Every command's output, recorded: what a user sees, held still (syrupy).

One journey through the real CLI on the real tools of the integration
fixtures -- a wrapper no installer claims (`faketool`), an npm package that
ships its page (`marked`) and one that does not (`cowsay`) -- with real
pandoc, groff and man. Each command's exit code, output and stderr are
compared with `__snapshots__/test_snapshots.ambr`.

A refactor must leave these identical (CLAUDE.md). A change meant to alter
what users see updates them on purpose: `just snapshots-update`, and the
diff of the `.ambr` file is the change, reviewed like code.

Held still on purpose:
- offline: maniac's one HTTP opener refuses, and git's proxy is a closed
  port, so every upstream check ends "did not complete" the same way;
- the login `$PATH` holds only the fixtures' tools, so no machine's
  `/usr/bin` leaks in; this shell's `$PATH` adds what maniac itself runs
  (man, pandoc, groff, git, node);
- the model is canned (`faketool.1.md`), no line wraps,
  temporary paths and machine-dependent counts normalized.
"""

import io
import os
import re
import shutil
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest
from rich.console import Console
from syrupy.assertion import SnapshotAssertion
from typer.testing import CliRunner

import maniac.cli as cli_module
from maniac.cli import app
from maniac.config import Config
from maniac.sources import pathcache

TOOLING = ("man", "pandoc", "groff", "git", "node")


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> object:
        raise URLError("offline (snapshot test)")

    monkeypatch.setattr("maniac.sources.docs.cache._open", refuse)
    closed = "http://127.0.0.1:9"
    for variable in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.setenv(variable, closed)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")


@pytest.fixture
def canned_model(monkeypatch: pytest.MonkeyPatch, model_response: str) -> None:
    monkeypatch.setattr(
        "maniac.generation.llm._complete_litellm",
        lambda request: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=model_response))]
        ),
    )
    monkeypatch.setattr("maniac.generation.llm._supports_reasoning", lambda m: False)


class Session:
    """Runs `maniac` as a user would, and normalizes what it prints."""

    def __init__(self, replacements: dict[str, str]) -> None:
        self._runner = CliRunner()
        self._replacements = replacements

    def __call__(self, *args: str, terminal: bool = False) -> str:
        screen = io.StringIO()
        console = Console(
            file=screen,
            force_terminal=terminal,
            # A terminal for tables, never animation: progress frames carry
            # timings, which would make every run differ.
            force_interactive=False,
            color_system=None,
            # Wide enough that no line wraps: where Rich wraps depends on
            # the temporary path's length, which differs by machine. Tables
            # size to their content either way.
            width=1000,
            highlight=False,
        )
        cli_module.console._instance = console
        try:
            result = self._runner.invoke(app, list(args), prog_name="maniac")
        finally:
            cli_module.console._instance = None
        text = (
            f"$ maniac {' '.join(args)}\n[exit {result.exit_code}]\n"
            + screen.getvalue()
            + result.stdout
            + (f"--- stderr\n{result.stderr}" if result.stderr else "")
        )
        if result.exception is not None and not isinstance(
            result.exception, SystemExit
        ):
            raise result.exception
        return self._normalize(text)

    def _normalize(self, text: str) -> str:
        # Terminal control (cursor hiding around a progress bar) is not content.
        text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
        for real, placeholder in self._replacements.items():
            text = text.replace(real, placeholder)
        # Counts of this machine's own `$PATH` directories.
        text = re.sub(
            r"started in \$HOME: \d+ director(y|ies)",
            "started in $HOME: <n> directories",
            text,
        )
        text = re.sub(
            r"\d+ more this shell has (is|are) left out",
            "<n> more this shell has are left out",
            text,
        )
        return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


@pytest.fixture
def maniac(
    faketool: Path,
    npm_prefix: Path,
    offline: None,
    canned_model: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Session:
    login = [faketool.parent, npm_prefix / "bin"]
    tooling = []
    for tool in TOOLING:
        found = shutil.which(tool)
        assert found is not None, f"the snapshot journey needs `{tool}`"
        if Path(found).parent not in tooling + login:
            tooling.append(Path(found).parent)
    login_path = os.pathsep.join(map(str, login))
    monkeypatch.setattr(pathcache, "_spawn_login_shell", lambda: login_path)
    pathcache.clear_login_path()
    monkeypatch.setenv("PATH", os.pathsep.join(map(str, login + tooling)))
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("MANPATH", str(Config().man_dir.parent))
    monkeypatch.setenv("COLUMNS", "120")
    # The canned model answers; this only lets maniac pick a model to ask.
    monkeypatch.setenv("OPENAI_API_KEY", "snapshot")
    monkeypatch.setenv("MANIAC_MODEL", "openai/gpt-4o-mini")
    return Session({str(npm_prefix): "<npm>", str(tmp_path): "<tmp>"})


def test_every_command_as_a_user_sees_it(
    maniac: Session, snapshot: SnapshotAssertion
) -> None:
    def shows(*args: str, terminal: bool = False, when: str = "") -> None:
        # Named after the command line, so adding a step never renumbers
        # the others and the `.ambr` diff reads as what changed.
        name = " ".join(("maniac", *args)) + (" (terminal)" if terminal else "")
        name += f" ({when})" if when else ""
        assert maniac(*args, terminal=terminal) == snapshot(name=name)

    # Get pages: generated, shipped, and one only upstream could give.
    shows("install", "faketool", "marked", "cowsay")
    shows("install", "marked", when="already current")
    shows("install", "marked", "--dry-run", "--force")
    shows("install", "cowsay", "--no-generate")
    shows("install", "nowhere-xyz")
    shows("install")

    # Look at them.
    shows("list", terminal=True)
    shows("list")
    shows("list", "cowsay", "faketool")
    shows("update")
    shows("update", "marked", "cowsay")

    # The rest of the machine.
    shows("scan", "cowsay", "marked", "faketool", terminal=True)
    shows("scan")
    shows("scan", "--missing")

    # Why.
    shows("why", "faketool")
    shows("why", "marked")
    shows("why", "cowsay")
    shows("why", "nowhere-xyz")

    # Take them out.
    shows("remove", "marked", "nowhere-xyz")
    shows("list", terminal=True, when="after remove")

    # The help a user reads.
    shows("--help")
    for command in ("install", "update", "remove", "list", "scan", "why"):
        shows(command, "--help")
