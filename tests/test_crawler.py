import subprocess
from typing import Any

import pytest

from maniac.crawler import find_subcommands, get_help
from maniac.exceptions import CrawlerError


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
    with pytest.raises(CrawlerError):
        get_help(["nonexistent_binary"])


def test_find_subcommands_recursive(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_help(cmd: list[str]) -> str:
        if cmd == ["tool"]:
            return "Commands:\n  sub1  First sub\n  sub2  Second sub"
        if cmd == ["tool", "sub1"]:
            return "Commands:\n  leaf  Leaf command"
        if cmd == ["tool", "sub1", "leaf"]:
            return "No subcommands"
        if cmd == ["tool", "sub2"]:
            return "No subcommands"
        return "No subcommands"

    monkeypatch.setattr("maniac.crawler.get_help", fake_get_help)
    tree = find_subcommands("tool")

    assert "> tool --help" in tree
    assert "> tool sub1 --help" in tree
    assert "> tool sub1 leaf --help" in tree
    assert "> tool sub2 --help" in tree
