"""Finding a tool's shell completion (ADR-0066): shipped, or its own generator."""

import stat
from pathlib import Path

import pytest

from maniac.sources.completions import (
    generated_completion,
    generator_commands,
    looks_like_completion,
    shipped_completion,
)

_SCRIPTS = {
    "bash": "_tool() { :; }\ncomplete -F _tool tool\n",
    "zsh": "#compdef tool\n_arguments '--x[x]'\n",
    "fish": "complete -c tool -l x\n",
}


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("shell", "relative"),
    [
        ("bash", "share/bash-completion/completions/tool"),
        ("zsh", "share/zsh/site-functions/_tool"),
        ("fish", "share/fish/vendor_completions.d/tool.fish"),
        ("zsh", "tool-v1.0-x86_64/autocomplete/_tool"),
        ("bash", "completions/tool.bash"),
        ("fish", "contrib/completion/fish/tool.fish"),
    ],
)
def test_a_shipped_completion_is_found_where_installs_put_them(
    tmp_path: Path, shell: str, relative: str
) -> None:
    root = tmp_path / "root"
    shipped = _write(root / relative, _SCRIPTS[shell])

    assert shipped_completion(root, "tool", shell) == shipped


def test_a_bare_name_counts_only_in_bashs_own_directory(tmp_path: Path) -> None:
    """`completions/tool` might be anything; `bash-completion/completions/tool`
    can only be bash's."""
    root = tmp_path / "root"
    _write(root / "completions" / "tool", _SCRIPTS["bash"])

    assert shipped_completion(root, "tool", "bash") is None


def test_a_file_that_is_not_a_completion_for_that_shell_is_passed_over(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    _write(root / "completions" / "_tool", _SCRIPTS["bash"])
    _write(root / "completions" / "tool.fish", "# fish key bindings\nbind \\cr x\n")

    assert shipped_completion(root, "tool", "zsh") is None
    assert shipped_completion(root, "tool", "fish") is None


def test_a_completion_resolving_outside_the_root_is_not_the_tools(
    tmp_path: Path,
) -> None:
    elsewhere = _write(tmp_path / "elsewhere" / "_tool", _SCRIPTS["zsh"])
    root = tmp_path / "root"
    (root / "completions").mkdir(parents=True)
    (root / "completions" / "_tool").symlink_to(elsewhere)

    assert shipped_completion(root, "tool", "zsh") is None


def test_generators_are_only_the_forms_the_help_names() -> None:
    assert generator_commands("Usage: tool [OPTIONS]", ["run"], "zsh") == []
    assert generator_commands("", ["completion"], "zsh")[0] == ("completion", "zsh")
    assert generator_commands("  --show-completion  Show completion", [], "fish") == [
        ("--show-completion", "fish")
    ]
    assert generator_commands(
        "--generate KIND  complete-bash, complete-zsh", [], "zsh"
    ) == [("--generate", "complete-zsh")]
    # A longer flag is not the shorter one.
    assert generator_commands("--completions-dir DIR", [], "zsh") == []


def _tool(tmp_path: Path, body: str) -> Path:
    tool = tmp_path / "bin" / "tool"
    _write(tool, f"#!/bin/sh\n{body}\n")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    return tool


def test_the_first_generator_form_that_prints_a_completion_wins(
    tmp_path: Path,
) -> None:
    """`completion zsh` fails here; `completion --shell zsh` answers."""
    tool = _tool(
        tmp_path,
        'if [ "$2" = "--shell" ]; then printf "#compdef tool\\n_tool() { }\\n"; '
        "else echo 'error: unexpected' >&2; exit 2; fi",
    )

    generated = generated_completion(
        tool, "tool", "zsh", help_text="", subcommands=["completion"], timeout=5
    )

    assert generated is not None
    assert generated.arguments == ("completion", "--shell", "zsh")
    assert generated.script.startswith("#compdef tool")


def test_output_that_is_not_a_completion_is_refused(tmp_path: Path) -> None:
    tool = _tool(tmp_path, "echo 'Usage: tool completion <shell>'")

    assert (
        generated_completion(
            tool, "tool", "bash", help_text="", subcommands=["completion"], timeout=5
        )
        is None
    )


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_a_completion_names_its_tool(shell: str) -> None:
    assert looks_like_completion(_SCRIPTS[shell], shell, "tool")
    assert not looks_like_completion(
        _SCRIPTS[shell].replace("tool", "x"), shell, "tool"
    )


@pytest.mark.parametrize(
    ("shell", "loader"),
    [
        ("bash", 'eval "$(JUST_COMPLETE=bash just)"\n'),
        ("fish", "JUST_COMPLETE=fish just | source\n"),
    ],
)
def test_a_loader_that_evaluates_the_tools_own_output_is_a_completion(
    shell: str, loader: str
) -> None:
    """`just --completions bash` prints a loader, not a script; the shell
    sources it the same way."""
    assert looks_like_completion(loader, shell, "just")


def test_a_hidden_generator_is_asked_only_of_tools_known_to_hide_one() -> None:
    assert generator_commands("", [], "zsh", "uv") == [
        ("generate-shell-completion", "zsh")
    ]
    assert generator_commands("", [], "zsh", "ssh") == []
