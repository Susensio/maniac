import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

# Well-known Rust / ecosystem mappings
KNOWN_TOOL_REPOS: dict[str, str] = {
    "cargo": "rust-lang/cargo",
    "cargo-clippy": "rust-lang/rust-clippy",
    "clippy-driver": "rust-lang/rust-clippy",
    "cargo-fmt": "rust-lang/rustfmt",
    "rustfmt": "rust-lang/rustfmt",
    "cargo-miri": "rust-lang/miri",
    "rust-analyzer": "rust-lang/rust-analyzer",
    "rustup": "rust-lang/rustup",
    "rustc": "rust-lang/rust",
    "rustdoc": "rust-lang/rust",
    "rust-gdb": "rust-lang/rust",
    "rust-gdbgui": "rust-lang/rust",
    "rust-lldb": "rust-lang/rust",
    "rls": "rust-lang/rls",
    "agy": "google-antigravity/antigravity-cli",
    "antigravity": "google-antigravity/antigravity-cli",
    "hx": "helix-editor/helix",
    "helix": "helix-editor/helix",
    "shfmt": "mvdan/sh",
    "uvx": "astral-sh/uv",
}


@dataclass
class RepoSource:
    name: str
    target: str  # "owner/repo" or "LOCAL:/path" or "https://..."
    is_local: bool
    local_path: Path | None = None

    @property
    def clone_url(self) -> str | None:
        if self.is_local:
            return None
        if self.target.startswith(("http://", "https://")):
            return self.target
        return f"https://github.com/{self.target}.git"


def discover_repo(binary_name: str, bin_dir: str | Path | None = None) -> RepoSource:
    """Discover upstream GitHub repository or local source for a binary."""
    # 1. Check known mappings first
    if binary_name in KNOWN_TOOL_REPOS:
        target = KNOWN_TOOL_REPOS[binary_name]
        logger.debug("Resolved '{}' from known tool map: {}", binary_name, target)
        return RepoSource(name=binary_name, target=target, is_local=False)

    bin_path = (
        Path(bin_dir) if bin_dir else Path.home() / ".local" / "bin"
    ) / binary_name

    # 2. If symlink, inspect target
    if bin_path.is_symlink():
        link_target = os.readlink(bin_path)
        resolved_path = (bin_path.parent / link_target).resolve()

        # Check local lib (e.g. ~/.local/lib/<tool>)
        if "/.local/lib/" in str(resolved_path):
            tool_dir = resolved_path
            while (
                tool_dir.parent != Path.home() / ".local" / "lib"
                and tool_dir != tool_dir.parent
            ):
                tool_dir = tool_dir.parent
            if (tool_dir / ".git").exists():
                git_remote = _get_git_remote(tool_dir)
                if git_remote:
                    clean_repo = _clean_git_url(git_remote)
                    return RepoSource(
                        name=binary_name, target=clean_repo, is_local=False
                    )
            return RepoSource(
                name=binary_name,
                target=f"LOCAL:{tool_dir}",
                is_local=True,
                local_path=tool_dir,
            )

        # Check uv tools (e.g. ~/.local/share/uv/tools/<tool>)
        if "/.local/share/uv/tools/" in str(resolved_path):
            local_proj = _find_uv_tool_local_dir(resolved_path)
            if local_proj:
                return RepoSource(
                    name=binary_name,
                    target=f"LOCAL:{local_proj}",
                    is_local=True,
                    local_path=local_proj,
                )

        # Check mise installs
        if "/.local/share/mise/installs/" in str(resolved_path):
            tool_id = _extract_mise_tool_id(resolved_path)
            if tool_id:
                repo = _resolve_mise_tool(tool_id)
                if repo:
                    return RepoSource(name=binary_name, target=repo, is_local=False)

    # 3. Try mise registry lookup
    mise_reg = _query_mise_registry(binary_name)
    if mise_reg:
        return RepoSource(name=binary_name, target=mise_reg, is_local=False)

    # 4. Fallback default to binary_name as search query or placeholder
    return RepoSource(name=binary_name, target=binary_name, is_local=False)


def _get_git_remote(directory: Path) -> str | None:
    try:
        res = subprocess.run(
            ["git", "-C", str(directory), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("Failed getting git remote for {}: {}", directory, e)
    return None


def _clean_git_url(url: str) -> str:
    url = url.strip()
    match = re.search(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?$", url)
    if match:
        return match.group(1)
    return url


def _find_uv_tool_local_dir(resolved_path: Path) -> Path | None:
    try:
        dist_infos = list(
            resolved_path.parents[2].glob("lib/**/site-packages/*.dist-info")
        )
        for d in dist_infos:
            direct_url = d / "direct_url.json"
            if direct_url.exists():
                data = json.loads(direct_url.read_text(encoding="utf-8"))
                raw_url = data.get("url", "")
                if raw_url.startswith("file://"):
                    p = Path(raw_url[7:])
                    if p.exists():
                        return p
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("Error inspecting uv tool path: {}", e)
    return None


def _extract_mise_tool_id(path: Path) -> str | None:
    parts = path.parts
    if "installs" in parts:
        idx = parts.index("installs")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None


def _resolve_mise_tool(tool_id: str) -> str | None:
    # 1. Search in ~/.config/mise config files for exact tool match
    mise_cfg_dir = Path.home() / ".config" / "mise"
    if mise_cfg_dir.exists():
        for cfg in mise_cfg_dir.glob("**/*.toml"):
            try:
                content = cfg.read_text(encoding="utf-8")
                # Look for "github:owner/repo" or "cargo:https://github.com/owner/repo"
                matches = re.findall(
                    r'"(github:[\w.-]+/[\w.-]+|cargo:https://github\.com/[\w.-]+/[\w.-]+)"',
                    content,
                )
                for m in matches:
                    sanitized = (
                        m.replace("github:", "")
                        .replace("cargo:https://github.com/", "")
                        .replace(".", "-")
                        .replace("/", "-")
                    )
                    if (
                        sanitized in tool_id
                        or tool_id in sanitized
                        or tool_id.endswith(m.split("/")[-1].replace(".", "-"))
                    ):
                        if m.startswith("github:"):
                            return m.split(":", 1)[1]
                        if m.startswith("cargo:https://github.com/"):
                            return m.replace("cargo:https://github.com/", "")
            except OSError:
                pass

    if tool_id.startswith("github-"):
        parts = tool_id[7:].split("-", 1)
        if len(parts) == 2:
            return f"{parts[0]}/{parts[1]}"
    if tool_id.startswith("pipx-"):
        pkg = tool_id[5:]
        return _query_mise_registry(pkg) or pkg
    if tool_id.startswith("cargo-https-github-com-"):
        clean = tool_id.replace("cargo-https-github-com-", "")
        parts = clean.split("-", 1)
        if len(parts) == 2:
            return f"{parts[0]}/{parts[1]}"
    return _query_mise_registry(tool_id)


def _query_mise_registry(tool: str) -> str | None:
    try:
        res = subprocess.run(
            ["mise", "registry", tool],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            output = res.stdout.strip()
            for token in output.split():
                if token.startswith(("aqua:", "github:")):
                    return token.split(":", 1)[1]
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("Error querying mise registry for {}: {}", tool, e)
    return None
