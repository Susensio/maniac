"""A tool's whole life through maniac, with the real crawler, pandoc, groff and man.

Only the model is replaced: `_complete_litellm` returns a canned response.
Everything around it -- crawling the binary's help, building the prompt,
cleaning the response, compiling, installing, recording, `man -w`
finding the page, `list` judging it, `uninstall` removing it -- runs as
it does for a user.
"""

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from maniac import manifest
from maniac.cli import app
from maniac.config import Config
from maniac.exceptions import UnsupportedPandoc
from maniac.installer import uninstall_manpage
from maniac.listing import ActionState, compute_rows
from maniac.manifest import Tier
from maniac.orchestration.install import run_install

runner = CliRunner()


@pytest.fixture
def prompts(monkeypatch: pytest.MonkeyPatch, model_response: str) -> list[str]:
    """Every prompt sent to the (canned) model, in order."""
    sent: list[str] = []

    def complete(request: dict[str, object]) -> object:
        messages = request["messages"]
        assert isinstance(messages, list)
        sent.append(messages[0]["content"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=model_response))]
        )

    monkeypatch.setattr("maniac.generation.llm._complete_litellm", complete)
    # LiteLLM's capability lookup may fetch its model map; nothing to ask here.
    monkeypatch.setattr(
        "maniac.generation.llm._supports_reasoning", lambda model: False
    )
    return sent


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch) -> Config:
    """The default config (XDG already redirected under tmp by the root
    conftest), with `man` searching only its man root."""
    cfg = Config()
    monkeypatch.setenv("MANPATH", str(cfg.man_dir.parent))
    return cfg


def _says(output: str, text: str) -> bool:
    """Whether `output` contains `text`, however Rich wrapped it."""
    return "".join(text.split()) in "".join(output.split())


def _man(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["man", *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "MANPAGER": "cat", "MANWIDTH": "100"},
    )


def test_install_list_upgrade_uninstall(
    faketool: Path, prompts: list[str], config: Config
) -> None:
    outcome = run_install("faketool", model="openai/gpt-4o-mini", config=config)

    page = config.man_dir / "faketool.1"
    assert outcome.tier is Tier.SYNTHESIS
    assert outcome.installed_path is not None
    # The page lives in maniac's own output dir; the man root links to it.
    assert page.resolve() == outcome.installed_path.resolve()

    # The crawler ran the real binary and recursed into its subcommands.
    [prompt] = prompts
    assert "Usage: faketool build [OPTIONS] [PATH]" in prompt
    assert "Usage: faketool serve [OPTIONS]" in prompt

    # `man` finds the installed page by name and renders it.
    assert Path(_man("-w", "faketool").stdout.strip()).resolve() == page.resolve()
    rendered = _man("faketool")
    assert rendered.returncode == 0, rendered.stderr
    assert "faketool - build and serve fake projects" in rendered.stdout

    # The footer shows the binary's own version line, once.
    assert '"faketool 2.3.1" "User Commands"' in page.read_text(encoding="utf-8")

    # Recorded with the binary's own `--version`, verbatim (ADR-0020).
    entry = manifest.lookup("faketool", config=config)
    assert entry is not None
    assert (entry.tier, entry.version) == (Tier.SYNTHESIS, "faketool 2.3.1")

    [row] = compute_rows(["faketool"], config=config)
    assert row.managed
    assert row.state is ActionState.OK

    # The tool upgrades; its page now documents an older version.
    faketool.write_text(
        faketool.read_text(encoding="utf-8").replace("2.3.1", "2.4.0"),
        encoding="utf-8",
    )
    [row] = compute_rows(["faketool"], config=config)
    assert row.state is ActionState.OUTDATED

    result = uninstall_manpage("faketool", config=config)

    assert result is not None
    assert not page.exists()
    assert manifest.lookup("faketool", config=config) is None
    assert _man("-w", "faketool").returncode != 0


def test_an_old_pandoc_is_refused_before_the_model_is_called(
    faketool: Path,
    prompts: list[str],
    config: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0064: a pandoc that reports 3.1.3 -- Ubuntu 24.04's -- found on
    `$PATH` and asked for real, stops the install before any prompt goes
    out, and says what to install instead."""
    old = tmp_path / "old-pandoc"
    old.mkdir()
    (old / "pandoc").write_text("#!/bin/sh\necho 'pandoc 3.1.3'\n", encoding="utf-8")
    (old / "pandoc").chmod(0o755)
    monkeypatch.setenv("PATH", f"{old}{os.pathsep}{os.environ['PATH']}")

    with pytest.raises(UnsupportedPandoc, match=r"pandoc 3\.1\.3 is too old"):
        run_install("faketool", model="openai/gpt-4o-mini", config=config)

    assert prompts == []
    assert not (config.man_dir / "faketool.1").exists()


def test_a_venv_copy_is_noticed_then_documented_with_here(
    faketool: Path,
    prompts: list[str],
    config: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CONTRACT.md rule 2 through the real CLI. Inside an activated venv the
    page still documents the global copy, and says this shell runs another;
    `--here` documents the venv's, and `list` keeps checking that copy."""
    from maniac.sources import pathcache

    venv_bin = tmp_path / "project" / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    venv_tool = venv_bin / "faketool"
    venv_tool.write_text(
        faketool.read_text(encoding="utf-8").replace("2.3.1", "3.0.0"),
        encoding="utf-8",
    )
    venv_tool.chmod(0o755)
    login_path = os.environ["PATH"]  # the global faketool, first
    monkeypatch.setattr(pathcache, "_spawn_login_shell", lambda: login_path)
    monkeypatch.setenv("PATH", f"{venv_bin}{os.pathsep}{login_path}")
    monkeypatch.setenv("VIRTUAL_ENV", str(venv_bin.parent))
    model = ["--model", "openai/gpt-4o-mini"]

    plain = runner.invoke(app, ["install", *model, "faketool"])

    assert plain.exit_code == 0, plain.output
    assert _says(
        plain.output, f"documents {faketool} (no installer; it reports faketool 2.3.1)"
    )
    assert _says(plain.output, f"this shell runs {venv_tool} instead")

    here = runner.invoke(app, ["install", *model, "--here", "faketool"])

    assert here.exit_code == 0, here.output
    assert _says(
        here.output, f"documents {venv_tool} (no installer; it reports faketool 3.0.0)"
    )
    assert not _says(here.output, "this shell runs")
    entry = manifest.lookup("faketool", config=config)
    assert entry is not None
    assert (entry.binary, entry.version) == (venv_tool, "faketool 3.0.0")
    page = config.man_dir / "faketool.1"
    assert '"faketool 3.0.0" "User Commands"' in page.read_text(encoding="utf-8")

    # `list` checks the recorded copy, not the global one, even from $HOME.
    monkeypatch.setenv("PATH", login_path)
    [row] = compute_rows(["faketool"], config=config)
    assert row.state is ActionState.OK
    venv_tool.write_text(
        venv_tool.read_text(encoding="utf-8").replace("3.0.0", "3.1.0"),
        encoding="utf-8",
    )
    [row] = compute_rows(["faketool"], config=config)
    assert row.state is ActionState.OUTDATED
