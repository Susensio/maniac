import subprocess
import sys
from typing import Any

import pytest

from maniac.exceptions import CrawlerError
from maniac.sources.crawler import (
    _run_cli_flag,
    extract_subcommands,
    find_subcommands,
    get_help,
    get_version,
)


def test_get_help_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git", "--help"],
            returncode=0,
            stdout="Usage: git [options]\n\nCommands:\n  commit  Record changes",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = get_help(["git"])
    assert "Usage: git" in out


def test_get_help_uses_stderr_when_stdout_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["tmux", "--help"],
            returncode=1,
            stdout="",
            stderr="usage: tmux [-2CDlNuVv] [-c shell-command]",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert get_help(["tmux"]).startswith("usage: tmux")


@pytest.mark.parametrize("returncode", [0, 1])
def test_get_help_rejects_empty_output(
    monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["tool", "--help"], returncode=returncode, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(CrawlerError, match="produced no help output"):
        get_help(["tool"])


def test_get_help_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["git", "--help"], timeout=5)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(CrawlerError):
        get_help(["git"])


def test_get_help_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(CrawlerError) as exc_info:
        get_help(["nonexistent_binary"])
    assert "Executable 'nonexistent_binary' not found on $PATH" in str(exc_info.value)


def test_run_cli_flag_appends_the_given_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(
        full_cmd: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = full_cmd
        return subprocess.CompletedProcess(args=full_cmd, returncode=0, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    _run_cli_flag(["tool"], "--some-flag", 5)
    assert captured["cmd"] == ["tool", "--some-flag"]


def test_get_version_returns_trimmed_string_for_a_real_binary() -> None:
    out = get_version([sys.executable])
    assert out is not None
    assert out == out.strip()
    assert "Python" in out


def test_get_version_returns_none_on_non_zero_exit() -> None:
    """`/usr/bin/false` ignores `--version` and always exits 1 -- a real
    non-zero exit, not a simulated one."""
    assert get_version(["false"]) is None


def test_get_version_returns_none_when_executable_not_found() -> None:
    assert get_version(["nonexistent_binary_xyz_123"]) is None


def test_get_version_returns_none_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["tool", "--version"], timeout=5)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert get_version(["tool"]) is None


def test_get_version_returns_none_on_empty_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["tool", "--version"], returncode=0, stdout="   \n"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert get_version(["tool"]) is None


def test_find_subcommands_recursive(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_help(
        cmd: list[str], timeout: int | None = None, config: Any | None = None
    ) -> str:
        if cmd == ["tool"]:
            return "Commands:\n  sub1  First sub\n  sub2  Second sub"
        if cmd == ["tool", "sub1"]:
            return "Commands:\n  leaf  Leaf command"
        if cmd == ["tool", "sub1", "leaf"]:
            return "No subcommands"
        if cmd == ["tool", "sub2"]:
            return "No subcommands"
        return "No subcommands"

    monkeypatch.setattr("maniac.sources.crawler.get_help", fake_get_help)
    tree = find_subcommands("tool")

    assert "> tool --help" in tree
    assert "> tool sub1 --help" in tree
    assert "> tool sub1 leaf --help" in tree
    assert "> tool sub2 --help" in tree


def test_extract_from_commands_section() -> None:
    sample = """
Usage: mytool <command> [options]

Commands:
  build    Build the project
  test     Run tests
  publish  Publish package

Options:
  -h, --help  Show help
"""
    assert extract_subcommands(sample) == ["build", "test", "publish"]


def test_extract_with_cmd_name_prefix() -> None:
    sample = """
Usage: npm <command>

Commands:
  npm install  Install dependencies
  npm test     Run tests
"""
    assert extract_subcommands(sample, cmd_name="npm") == ["install", "test"]


def test_extract_actions_section() -> None:
    sample = """
Available actions:
    init       Initialize directory
    check      Check status
"""
    assert extract_subcommands(sample) == ["init", "check"]


def test_no_commands_section() -> None:
    sample = """
Usage: echo [STRING]...
Echo the STRING(s) to standard output.
"""
    assert extract_subcommands(sample) == []


def test_extract_box_table_rich() -> None:
    sample = """
╭─ Commands ─────────────────────────────────────────────────────────────╮
│ build    Build the project                                             │
│ test     Run tests                                                     │
╰────────────────────────────────────────────────────────────────────────╯
"""
    assert extract_subcommands(sample) == ["build", "test"]


def test_extract_box_table_with_continuation() -> None:
    sample = """
Commands:
+-------------------+---------------------------------------------------+
| Command           | Description                                       |
+-------------------+---------------------------------------------------+
| build             | Build the project                                 |
|                   | including all submodules and assets               |
| test              | Run tests                                         |
+-------------------+---------------------------------------------------+
"""
    assert extract_subcommands(sample) == ["build", "test"]


def test_extract_dotted_and_custom_subcommands() -> None:
    sample = """
Commands:
  todo.sh      Manage todos
  db:migrate   Run migrations
  clean_all    Clean all caches
"""
    assert extract_subcommands(sample) == ["todo.sh", "db:migrate", "clean_all"]


def test_extract_uppercase_header_with_options_stopper() -> None:
    sample = """
SUBCOMMANDS
  create    Create a resource
  delete    Delete a resource

OPTIONS
  --verbose Enable verbose logging
"""
    assert extract_subcommands(sample) == ["create", "delete"]
