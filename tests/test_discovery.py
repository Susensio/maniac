from pathlib import Path

from maniac.sources.discovery import (
    _check_mise_toml,
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
    assert (
        _resolve_from_mise("cargo-https-github-com-sharkdp-bat", "bat") == "sharkdp/bat"
    )


def test_check_mise_toml_tool_alias(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        """
[tool_alias]
gh-cli = "github:cli/cli"
custom = "owner/custom"
""",
        encoding="utf-8",
    )
    assert _check_mise_toml(cfg, "gh-cli", "gh") == "cli/cli"
    assert _check_mise_toml(cfg, "other", "custom") == "owner/custom"
    assert _check_mise_toml(cfg, "unknown", "unknown") is None


def test_check_mise_toml_tools_github_and_cargo(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        """
[tools]
"github:sharkdp/fd" = "latest"
"cargo:https://github.com/BurntSushi/ripgrep" = "latest"
"github:junegunn/fzf" = { filter_bins = ["fzf-tmux"] }
""",
        encoding="utf-8",
    )
    assert _check_mise_toml(cfg, "fd", "fd") == "sharkdp/fd"
    assert _check_mise_toml(cfg, "ripgrep", "rg") == "BurntSushi/ripgrep"
    assert _check_mise_toml(cfg, "unknown", "fzf-tmux") == "junegunn/fzf"
    assert _check_mise_toml(cfg, "unknown", "unknown") is None


def test_check_mise_toml_invalid(tmp_path: Path) -> None:
    cfg = tmp_path / "invalid.toml"
    cfg.write_text("invalid = [toml", encoding="utf-8")
    assert _check_mise_toml(cfg, "foo", "foo") is None

    missing = tmp_path / "nonexistent.toml"
    assert _check_mise_toml(missing, "foo", "foo") is None


def test_discover_repo_fallback(tmp_path: Path) -> None:
    source = discover_repo("nonexistent_unknown_tool", bin_dir=tmp_path)
    assert source.name == "nonexistent_unknown_tool"
    assert source.target == "nonexistent_unknown_tool"
