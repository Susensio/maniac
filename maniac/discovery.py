import json
import re
import subprocess
import tomllib
from pathlib import Path

from loguru import logger

from maniac.models import RepoSource


def discover_repo(binary_name: str, bin_dir: str | Path | None = None) -> RepoSource:
    """Discover upstream repository or local source for a binary dynamically via mise and system metadata."""
    target_bin_dir = Path(bin_dir) if bin_dir else Path.home() / ".local" / "bin"
    bin_path = target_bin_dir / binary_name

    # 1. Resolve through symlink inspection if applicable
    if bin_path.is_symlink():
        source = _resolve_symlink_target(binary_name, bin_path)
        if source:
            return source

    # 2. Query mise configuration and live registry
    repo_from_mise = _resolve_from_mise(binary_name, binary_name)
    if repo_from_mise:
        return RepoSource(name=binary_name, target=repo_from_mise, is_local=False)

    # 3. Fallback default
    return RepoSource(name=binary_name, target=binary_name, is_local=False)


def _resolve_symlink_target(binary_name: str, bin_path: Path) -> RepoSource | None:
    """Inspect resolved path of symlinked binary for mise, local lib, or uv installs."""
    resolved_path = bin_path.resolve()
    resolved_str = str(resolved_path)

    # Local lib directory (e.g. ~/.local/lib/<tool>)
    if "/.local/lib/" in resolved_str:
        return _resolve_local_lib(binary_name, resolved_path)

    # uv tools (e.g. ~/.local/share/uv/tools/<tool>)
    if "/.local/share/uv/tools/" in resolved_str:
        local_proj = _find_uv_tool_local_dir(resolved_path)
        if local_proj:
            return RepoSource(
                name=binary_name,
                target=f"LOCAL:{local_proj}",
                is_local=True,
                local_path=local_proj,
            )

    # mise installs (e.g. ~/.local/share/mise/installs/<tool>/...)
    if "/.local/share/mise/installs/" in resolved_str:
        tool_id = _extract_mise_tool_id(resolved_path)
        if tool_id:
            repo = _resolve_from_mise(tool_id, binary_name)
            if repo:
                return RepoSource(name=binary_name, target=repo, is_local=False)

    return None


def _resolve_local_lib(binary_name: str, resolved_path: Path) -> RepoSource:
    """Resolve repository source from a ~/.local/lib installation."""
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
            return RepoSource(name=binary_name, target=clean_repo, is_local=False)

    return RepoSource(
        name=binary_name,
        target=f"LOCAL:{tool_dir}",
        is_local=True,
        local_path=tool_dir,
    )


def _resolve_from_mise(tool_id: str, binary_name: str) -> str | None:
    """Infer repository dynamically from mise configuration files and mise registry."""
    # Step A: Parse user's mise config files (~/.config/mise/config.toml, etc.)
    mise_cfg_dir = Path.home() / ".config" / "mise"
    if mise_cfg_dir.exists():
        for cfg_path in mise_cfg_dir.glob("**/*.toml"):
            try:
                data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
                # Check tool_alias (e.g. antigravity = "github:google-antigravity/antigravity-cli")
                aliases = data.get("tool_alias", {})
                for alias_name, alias_target in aliases.items():
                    if alias_name in (tool_id, binary_name):
                        if alias_target.startswith("github:"):
                            return alias_target.split(":", 1)[1]
                        return alias_target

                # Check [tools] table keys
                tools = data.get("tools", {})
                for raw_tool_key, val in tools.items():
                    if raw_tool_key.startswith("github:"):
                        repo = raw_tool_key.split(":", 1)[1]
                        if (
                            tool_id in repo
                            or binary_name in repo
                            or _match_mise_filter_bins(val, binary_name)
                        ):
                            return repo
                    elif raw_tool_key.startswith("cargo:http"):
                        url = raw_tool_key.split(":", 1)[1]
                        if tool_id in url or binary_name in url:
                            return _clean_git_url(url)
                    elif raw_tool_key in (tool_id, binary_name):
                        reg = _query_mise_registry(raw_tool_key)
                        if reg:
                            return reg
            except (OSError, tomllib.TOMLDecodeError) as e:
                logger.debug("Error parsing mise config {}: {}", cfg_path, e)

    # Step B: Parse sanitized tool prefixes
    if tool_id.startswith("github-"):
        parts = tool_id[7:].split("-", 1)
        if len(parts) == 2:
            return f"{parts[0]}/{parts[1]}"
    if tool_id.startswith("pipx-"):
        pkg = tool_id[5:]
        return _query_mise_registry(pkg) or pkg
    if tool_id.startswith("npm-"):
        pkg = tool_id[4:]
        return _query_mise_registry(pkg) or pkg
    if tool_id.startswith("cargo-https-github-com-"):
        clean = tool_id.replace("cargo-https-github-com-", "")
        parts = clean.split("-", 1)
        if len(parts) == 2:
            return f"{parts[0]}/{parts[1]}"

    # Step C: Query mise registry dynamically
    return _query_mise_registry(tool_id) or _query_mise_registry(binary_name)


def _match_mise_filter_bins(tool_val: object, binary_name: str) -> bool:
    if isinstance(tool_val, dict):
        filter_bins = tool_val.get("filter_bins")
        if filter_bins and binary_name in str(filter_bins):
            return True
    return False


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
                    p = Path(raw_url.removeprefix("file://"))
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
