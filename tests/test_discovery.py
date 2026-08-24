from pathlib import Path

from maniac.discovery import (
    _clean_git_url,
    _extract_mise_tool_id,
    _resolve_from_mise,
    discover_repo,
)


def test_clean_git_url() -> None:
    assert _clean_git_url("https://github.com/gleitz/howdoi.git") == "gleitz/howdoi"
    assert _clean_git_url("git@github.com:astral-sh/uv.git") == "astral-sh/uv"
    assert (
        _clean_git_url("https://github.com/helix-editor/helix") == "helix-editor/helix"
    )


def test_extract_mise_tool_id() -> None:
    p = Path("/home/user/.local/share/mise/installs/glow/2.1.2/glow")
    assert _extract_mise_tool_id(p) == "glow"

    p_non_mise = Path("/usr/local/bin/something")
    assert _extract_mise_tool_id(p_non_mise) is None


def test_resolve_from_mise_prefixed() -> None:
    assert (
        _resolve_from_mise("github-todotxt-todo.txt-cli", "todo.sh")
        == "todotxt/todo.txt-cli"
    )


def test_discover_repo_fallback(tmp_path: Path) -> None:
    source = discover_repo("nonexistent_unknown_tool", bin_dir=tmp_path)
    assert source.name == "nonexistent_unknown_tool"
    assert source.target == "nonexistent_unknown_tool"
