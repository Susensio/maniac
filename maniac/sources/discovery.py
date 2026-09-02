"""Dynamic repository discovery via local metadata and Mise's registry."""

import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tomllib
from functools import cache
from pathlib import Path
from time import time
from urllib.error import URLError
from urllib.request import Request, urlopen

import zstandard

from ..logging import logger
from ..models import RepoSource

MISE_REGISTRY_URL = "https://mise.jdx.dev/registry/latest.tar.zst"
MISE_REGISTRY_TTL_SECONDS = 3_600


def discover_repo(binary_name: str, bin_dir: str | Path | None = None) -> RepoSource:
    """Discover an upstream repository or local source for a binary dynamically."""
    target_bin_dir = Path(bin_dir) if bin_dir else Path.home() / ".local" / "bin"
    bin_path = target_bin_dir / binary_name

    # If target binary doesn't exist in target_bin_dir, check system PATH
    if not bin_path.exists():
        which_path = shutil.which(binary_name)
        if which_path:
            bin_path = Path(which_path)

    # 1. Resolve through symlink inspection if applicable
    if bin_path.is_symlink():
        source = _resolve_symlink_target(binary_name, bin_path)
        if source:
            return source

    # 2. Query optional Mise configuration and the cached official registry
    repo_from_mise = _resolve_from_mise(binary_name, binary_name)
    if repo_from_mise:
        return RepoSource(name=binary_name, target=repo_from_mise, is_local=False)

    # 3. Fallback default
    return RepoSource(name=binary_name, target=binary_name, is_local=False)


def discover_candidate_source(binary_name: str) -> RepoSource | None:
    """Return a source proven by an installed executable, if one is available."""
    which_path = shutil.which(binary_name)
    if which_path is None:
        return None
    bin_path = Path(which_path)
    if not bin_path.is_symlink():
        return None
    return _resolve_symlink_target(
        binary_name, bin_path, allow_binary_registry_match=False
    )


def _resolve_symlink_target(
    binary_name: str, bin_path: Path, *, allow_binary_registry_match: bool = True
) -> RepoSource | None:
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
            repo = _resolve_from_mise(
                tool_id,
                binary_name,
                allow_binary_registry_match=allow_binary_registry_match,
            )
            if repo:
                return RepoSource(name=binary_name, target=repo, is_local=False)

    return None


def _resolve_local_lib(binary_name: str, resolved_path: Path) -> RepoSource:
    """Resolve repository source from a ~/.local/lib installation."""
    base_lib = Path.home() / ".local" / "lib"
    tool_dir = resolved_path
    if resolved_path.is_relative_to(base_lib):
        while tool_dir.parent != base_lib and tool_dir != tool_dir.parent:
            tool_dir = tool_dir.parent
    else:
        tool_dir = resolved_path.parent

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


def _check_mise_toml(cfg_path: Path, tool_id: str, binary_name: str) -> str | None:
    """Inspect a mise TOML config file for tool aliases or tool repository definitions."""
    try:
        data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.debug("Error parsing mise config", path=str(cfg_path), error=str(e))
        return None

    aliases = data.get("tool_alias", {})
    for alias_name, alias_target in aliases.items():
        if alias_name in (tool_id, binary_name):
            if alias_target.startswith("github:"):
                return alias_target.split(":", 1)[1]
            return alias_target

    tools = data.get("tools", {})
    for raw_tool_key, val in tools.items():
        if raw_tool_key.startswith("github:"):
            repo = raw_tool_key.split(":", 1)[1]
            if repo.rsplit("/", 1)[-1] in (
                tool_id,
                binary_name,
            ) or _match_mise_filter_bins(val, binary_name):
                return repo
        elif raw_tool_key.startswith("cargo:http"):
            url = raw_tool_key.split(":", 1)[1]
            package = url.rstrip("/").rsplit("/", 1)[-1]
            if package in (tool_id, binary_name):
                return _clean_git_url(url)
    return None


def _resolve_from_mise(
    tool_id: str, binary_name: str, *, allow_binary_registry_match: bool = True
) -> str | None:
    """Infer a repository from Mise configuration files and registry data."""
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    mise_cfg_dir = xdg_config / "mise"
    if mise_cfg_dir.exists():
        for cfg_path in mise_cfg_dir.glob("**/*.toml"):
            found = _check_mise_toml(cfg_path, tool_id, binary_name)
            if found:
                return found

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

    repo = _query_mise_registry(tool_id)
    if repo or not allow_binary_registry_match:
        return repo
    return _query_mise_registry(binary_name)


def _match_mise_filter_bins(tool_val: object, binary_name: str) -> bool:
    if isinstance(tool_val, dict):
        filter_bins = tool_val.get("filter_bins")
        if isinstance(filter_bins, str):
            return filter_bins == binary_name
        if isinstance(filter_bins, list):
            return binary_name in filter_bins
    return False


def _query_mise_registry(tool: str) -> str | None:
    """Look up a tool in the official Mise registry without invoking Mise."""
    return _load_mise_registry().get(tool)


@cache
def _load_mise_registry() -> dict[str, str]:
    """Load short names, aliases, and bins from Mise's cached registry archive."""
    archive = _read_mise_registry_archive()
    if archive is None:
        return {}

    try:
        return _parse_mise_registry(archive)
    except (
        OSError,
        tarfile.TarError,
        tomllib.TOMLDecodeError,
        UnicodeDecodeError,
        zstandard.ZstdError,
    ) as e:
        logger.debug("Unable to parse Mise registry", error=str(e))
        return {}


def _read_mise_registry_archive() -> bytes | None:
    """Read the fresh archive or download it once for the local cache."""
    cache_path = _mise_registry_cache_path()
    try:
        if (
            cache_path.is_file()
            and time() - cache_path.stat().st_mtime < MISE_REGISTRY_TTL_SECONDS
        ):
            return cache_path.read_bytes()
    except OSError as e:
        logger.debug("Unable to read cached Mise registry", error=str(e))

    try:
        request = Request(MISE_REGISTRY_URL, headers={"User-Agent": "maniac/0.1"})
        with urlopen(request, timeout=10) as response:
            archive = response.read()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = cache_path.with_suffix(".tmp")
        temporary_path.write_bytes(archive)
        temporary_path.replace(cache_path)
        return archive
    except (OSError, URLError) as e:
        logger.debug("Unable to download Mise registry", error=str(e))
        try:
            return cache_path.read_bytes()
        except OSError:
            return None


def _mise_registry_cache_path() -> Path:
    """Return MANIAC's XDG cache location for Mise registry data."""
    cache_home = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return cache_home / "maniac" / "mise-registry.tar.zst"


def _parse_mise_registry(archive: bytes) -> dict[str, str]:
    """Map Mise tool names, aliases, and bins to GitHub repository names."""
    canonical_names: dict[str, str] = {}
    alternate_names: dict[str, str] = {}
    decompressor = zstandard.ZstdDecompressor()
    with (
        decompressor.stream_reader(io.BytesIO(archive)) as stream,
        tarfile.open(fileobj=stream, mode="r|") as tar,
    ):
        for member in tar:
            if (
                not member.isfile()
                or not member.name.startswith("registry/")
                or not member.name.endswith(".toml")
            ):
                continue
            contents = tar.extractfile(member)
            if contents is None:
                continue
            entry = tomllib.loads(contents.read().decode("utf-8"))
            repo = _mise_entry_repo(entry)
            if repo is None:
                continue
            short_name = Path(member.name).stem
            canonical_names[short_name] = repo
            for name in _mise_entry_names(entry):
                alternate_names.setdefault(name, repo)
    registry = alternate_names
    registry.update(canonical_names)
    return registry


def _mise_entry_names(entry: dict[str, object]) -> list[str]:
    """Return a registry entry's aliases and binary names."""
    names: list[str] = []
    for key in ("aliases", "bins"):
        value = entry.get(key)
        if isinstance(value, str):
            names.append(value)
        elif isinstance(value, list):
            names.extend(item for item in value if isinstance(item, str))
    return names


def _mise_entry_repo(entry: dict[str, object]) -> str | None:
    """Return the first GitHub-backed registry backend."""
    backends = entry.get("backends")
    if not isinstance(backends, list):
        return None
    for backend in backends:
        if not isinstance(backend, str):
            continue
        if backend.startswith("github:"):
            return backend.split(":", 1)[1]
        if backend.startswith("aqua:"):
            package = backend.split(":", 1)[1].split("/")
            if len(package) >= 2:
                return "/".join(package[:2])
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
        logger.debug(
            "Failed getting git remote", directory=str(directory), error=str(e)
        )
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
            resolved_path.parents[1].glob("lib/**/site-packages/*.dist-info")
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
    except (OSError, IndexError, json.JSONDecodeError) as e:
        logger.debug("Error inspecting uv tool path", error=str(e))
    return None


def _extract_mise_tool_id(path: Path) -> str | None:
    parts = path.parts
    if "installs" in parts:
        idx = parts.index("installs")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None
