"""Mise registry and configuration lookups behind provider source resolution.

Deliberately depends on no provider and no resolver: `providers/mise.py` and
the URL cleaning its peers share both import this, so anything here reaching
back up would close a cycle. Resolving a binary to a provider lives in
`resolution.py`; naming a binary at all lives in `pathcache.py`.
"""

import io
import re
import tarfile
import tempfile
import threading
import tomllib
from pathlib import Path
from time import time
from urllib.error import URLError
from urllib.request import Request, urlopen

import zstandard

from ..config import Config
from ..exceptions import MalformedToolMetadata
from ..logging import logger

MISE_REGISTRY_URL = "https://mise.jdx.dev/registry/latest.tar.zst"
MISE_REGISTRY_TTL_SECONDS = 3_600


class _MiseRegistryLoader:
    """Process-local, per-cache-path single-flight registry loader."""

    def __init__(self) -> None:
        self._cache: dict[Path, dict[str, str]] = {}
        self._locks: dict[Path, threading.Lock] = {}
        self._guard = threading.Lock()

    def __call__(self, cache_path: Path) -> dict[str, str]:
        with self._guard:
            cached = self._cache.get(cache_path)
            if cached is not None:
                return cached
            lock = self._locks.setdefault(cache_path, threading.Lock())

        with lock:
            with self._guard:
                cached = self._cache.get(cache_path)
                if cached is not None:
                    return cached

            registry = _load_mise_registry_uncached(cache_path)
            with self._guard:
                self._cache[cache_path] = registry
            return registry

    def cache_clear(self) -> None:
        with self._guard:
            self._cache.clear()


def _check_mise_toml(cfg_path: Path, tool_id: str, binary_name: str) -> str | None:
    """Inspect a mise TOML config file for tool aliases or tool repository definitions."""
    try:
        data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise MalformedToolMetadata(cfg_path, str(e)) from e

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
    tool_id: str, binary_name: str, *, config: Config, offline: bool = False
) -> str | None:
    """Infer a repository from Mise configuration files, then the registry.

    `tool_id` is looked up as-is -- no fallback to `binary_name` in the
    registry query. Both must be installation-derived (ADR-0008): the caller
    is `MiseProvider.resolve_source`, keying on the install directory name
    it detected, never a bare command-line name with no installation behind
    it (ADR-0015's ruling on Stage 2's gap).

    `offline=True` skips `_query_mise_registry`, the one step here that can
    reach the network (`_read_mise_registry_archive`'s `urlopen`, on a cache
    miss or a stale TTL). Filesystem configuration and backend resolution are
    still attempted in either mode; callers that leave it false permit the
    registry fallback and its cache refresh.
    """
    mise_cfg_dir = config.config_dir / "mise"
    if mise_cfg_dir.exists():
        for cfg_path in mise_cfg_dir.glob("**/*.toml"):
            found = _check_mise_toml(cfg_path, tool_id, binary_name)
            if found:
                return found

    if offline:
        return None
    return _query_mise_registry(tool_id, config=config)


def _match_mise_filter_bins(tool_val: object, binary_name: str) -> bool:
    if isinstance(tool_val, dict):
        filter_bins = tool_val.get("filter_bins")
        if isinstance(filter_bins, str):
            return filter_bins == binary_name
        if isinstance(filter_bins, list):
            return binary_name in filter_bins
    return False


def _query_mise_registry(tool: str, *, config: Config) -> str | None:
    """Look up a tool in the official Mise registry without invoking Mise."""
    return _load_mise_registry(_mise_registry_cache_path(config)).get(tool)


def _load_mise_registry_uncached(cache_path: Path) -> dict[str, str]:
    """Load short names, aliases, and bins from Mise's cached registry archive."""
    archive = _read_mise_registry_archive(cache_path)
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
        raise MalformedToolMetadata(cache_path, str(e)) from e


_load_mise_registry = _MiseRegistryLoader()


def _read_mise_registry_archive(cache_path: Path) -> bytes | None:
    """Read the fresh archive from cache, or download it once for the local cache.

    A cache file that vanishes mid-check (`FileNotFoundError`) is an
    ordinary race with a concurrent write, treated the same as never having
    had one. Any other read failure -- permissions, an I/O error -- means
    the cache is present but broken, reported rather than silently routed
    around by falling through to a network refetch (ADR-0060).
    """
    try:
        fresh = (
            cache_path.is_file()
            and time() - cache_path.stat().st_mtime < MISE_REGISTRY_TTL_SECONDS
        )
    except FileNotFoundError:
        fresh = False
    if fresh:
        try:
            return cache_path.read_bytes()
        except FileNotFoundError:
            pass
        except OSError as e:
            raise MalformedToolMetadata(cache_path, str(e)) from e

    try:
        request = Request(MISE_REGISTRY_URL, headers={"User-Agent": "maniac/0.1"})
        with urlopen(request, timeout=10) as response:
            archive = response.read()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=cache_path.parent,
                prefix=f".{cache_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as file:
                file.write(archive)
                temporary_path = Path(file.name)
            temporary_path.replace(cache_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return archive
    except (OSError, URLError) as e:
        logger.debug("Unable to download Mise registry", error=str(e))
        try:
            return cache_path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as read_error:
            raise MalformedToolMetadata(cache_path, str(read_error)) from read_error


def _mise_registry_cache_path(config: Config) -> Path:
    """Return MANIAC's cache location for Mise registry data."""
    return config.cache_dir.parent / "mise-registry.tar.zst"


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


def _clean_git_url(url: str) -> str:
    # TODO: non-GitHub hosts (GitLab, Codeberg) are unresolved on purpose -- npm, pipx, uv,
    # go, homebrew and cargo discard any repository URL this function leaves unchanged, so
    # such a project resolves to nothing even when its metadata declares the URL. Trigger: a
    # real installed tool resolving to a non-GitHub host; the fix is one cross-provider
    # policy, not six provider patches.
    url = url.strip()
    match = re.search(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url)
    if match:
        return match.group(1)
    return url


def _extract_mise_tool_id(path: Path) -> str | None:
    parts = path.parts
    if "installs" in parts:
        idx = parts.index("installs")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None
