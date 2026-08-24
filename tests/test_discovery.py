from pathlib import Path

from maniac.discovery import (
    _clean_git_url,
    _extract_mise_tool_id,
    _resolve_mise_tool,
    discover_repo,
)


def test_known_tool_repos_lookup() -> None:
    source = discover_repo("cargo")
    assert source.name == "cargo"
    assert source.target == "rust-lang/cargo"
    assert not source.is_local
    assert source.clone_url == "https://github.com/rust-lang/cargo.git"


def test_known_tool_helix() -> None:
    source = discover_repo("hx")
    assert source.target == "helix-editor/helix"


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


def test_resolve_mise_tool() -> None:
    assert _resolve_mise_tool("github-todotxt-todo-txt-cli") == "todotxt/todo.txt-cli"
    assert _resolve_mise_tool("cargo-https-github-com-nushell-nufmt") == "nushell/nufmt"


def test_discover_repo_fallback(tmp_path: Path) -> None:
    source = discover_repo("nonexistent_unknown_tool", bin_dir=tmp_path)
    assert source.name == "nonexistent_unknown_tool"
    assert source.target == "nonexistent_unknown_tool"
