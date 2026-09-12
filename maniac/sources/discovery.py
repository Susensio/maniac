"""Dynamic repository discovery via local metadata and Mise's registry."""

import io
import os
import re
import tarfile
import tempfile
import threading
import tomllib
from collections.abc import Callable
from pathlib import Path
from time import time
from typing import TYPE_CHECKING
from urllib.error import URLError
from urllib.request import Request, urlopen

import zstandard

from ..config import Config
from ..logging import logger
from ..models import Installation, RepoSource
from . import loginpath

if TYPE_CHECKING:
    from .providers.base import Provider

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


def resolve_bin_path(binary_name: str, bin_dir: str | Path | None) -> Path | None:
    """Locate a binary's path: an explicit directory first, then the login `$PATH`.

    With no `bin_dir`, resolution is exactly `loginpath.which_login` --
    nothing else. `enumerate_installations` applies the identical first-
    `$PATH`-entry-wins rule in bulk, by walking the login `$PATH` itself
    once for every name rather than calling `which_login` once per name;
    that walk, not a second notion of "which binary a name means", is the
    only reason the mechanics differ here.

    An explicit `bin_dir` is stated intent -- a directory the caller named,
    not one MANIAC found -- and is checked first regardless of what the
    login `$PATH` would have resolved to.
    """
    if bin_dir is not None:
        explicit_path = Path(bin_dir) / binary_name
        if explicit_path.exists():
            return explicit_path
    return loginpath.which_login(binary_name)


def discover_repo(
    binary_name: str, bin_dir: str | Path | None = None, *, config: Config
) -> RepoSource | None:
    """Discover an upstream repository or local source for a binary dynamically.

    None means unresolvable: no provider (ADR-0015) detected an
    installation behind this binary, so nothing installation-derived backs
    a source for it. There is no bare-name fallback -- ADR-0015 rules that
    "a tool with no provider is reported as unresolvable and nothing is
    generated for it", closing the gap where this used to return
    `RepoSource(name=binary, target=binary)`, reachable by `run_pipeline`
    and liable to synthesize a page for a genuinely unresolved tool.
    """
    bin_path = resolve_bin_path(binary_name, bin_dir)
    if bin_path is None:
        return None
    return _resolve_symlink_target(binary_name, bin_path, config=config)


def find_installation(
    binary_name: str, bin_dir: str | Path | None = None
) -> "tuple[Provider, Installation] | None":
    """Return the provider and `Installation` a binary resolves to, if any.

    Shares `discover_repo`'s own bin-path resolution but stops at the
    `Installation` itself rather than resolving its source -- ADR-0016's
    tier 1 (install root) and tier 2 (repository, version matched) both
    need the install root and version directly, not only what
    `resolve_source` derives from them.
    """
    bin_path = resolve_bin_path(binary_name, bin_dir)
    if bin_path is None:
        return None
    return _detect_via_registry(bin_path)


def enumerate_installations(
    on_start: Callable[[int], None] | None = None,
    on_scan: Callable[[], None] | None = None,
    on_found: Callable[["Provider", Installation], None] | None = None,
) -> "list[tuple[Provider, Installation]]":
    """Walk the login `$PATH` once per unique binary name, resolved through the provider registry.

    `status`'s enumeration (ADR-0016 Stage 7) inverts from scanning the
    manpath to this: a binary is a unit MANIAC can act on because some
    provider claims it, whether or not a manpage for it exists anywhere
    yet -- capability the manpath scan could never see, not a page. A name
    is resolved once, at its first `$PATH` occurrence, since that is the
    binary that actually runs when two providers claim the same name
    (ADR-0016's tie-break).

    Walks `loginpath.login_path_dirs()` (ADR-0020) rather than the `$PATH`
    MANIAC inherited, so the answer describes the machine rather than the
    shell that happened to invoke this.

    `on_start`/`on_scan`, both `None` by default, split the work into two
    phases to instrument: `on_start` fires once with the candidate count,
    right after the directory scan and before the per-candidate
    `_detect_via_registry` loop that dominates the cost; `on_scan` fires
    once per candidate processed in that loop.
    """
    seen: dict[str, Path] = {}
    for entry in loginpath.login_path_dirs():
        try:
            children = list(os.scandir(entry))
        except OSError:
            continue
        for child in children:
            if child.name in seen:
                continue
            try:
                if not child.is_file() or not os.access(child.path, os.X_OK):
                    continue
            except OSError:
                continue
            seen[child.name] = Path(child.path)

    if on_start is not None:
        on_start(len(seen))

    found: list[tuple[Provider, Installation]] = []
    for bin_path in seen.values():
        claim = _detect_via_registry(bin_path)
        if claim is not None:
            found.append(claim)
            if on_found is not None:
                on_found(*claim)
        if on_scan is not None:
            on_scan()

    return sorted(found, key=lambda item: item[1].binary)


def _detect_via_registry(bin_path: Path) -> "tuple[Provider, Installation] | None":
    """Loop over registered providers (ADR-0015) for the one that claims this path."""
    from .providers import registry  # deferred: providers import this module themselves

    for provider in registry.candidates_for(bin_path):
        inst = provider.detect(bin_path)
        if inst is not None:
            return provider, inst
    return None


def _resolve_symlink_target(
    binary_name: str, bin_path: Path, *, config: Config
) -> RepoSource | None:
    found = _detect_via_registry(bin_path)
    if found is None:
        return None
    provider, inst = found
    return provider.resolve_source(inst, config=config)


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
    tool_id: str, binary_name: str, *, config: Config, offline: bool = False
) -> str | None:
    """Infer a repository from Mise configuration files, then the registry.

    `tool_id` is looked up as-is -- no fallback to `binary_name` in the
    registry query. Both must be installation-derived (ADR-0008): the caller
    is `MiseProvider.resolve_source`, keying on the install directory name
    it detected, never a bare command-line name with no installation behind
    it (ADR-0015's ruling on Stage 2's gap).

    `offline` skips `_query_mise_registry`, the one step here that can reach
    the network (`_read_mise_registry_archive`'s `urlopen`, on a cache miss
    or a stale TTL) -- `maniac list` (ADR-0018) sets it to keep that command
    at zero network I/O; `install` leaves it at the default and may still
    hit the network.
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
        logger.debug("Unable to parse Mise registry", error=str(e))
        return {}


_load_mise_registry = _MiseRegistryLoader()


def _read_mise_registry_archive(cache_path: Path) -> bytes | None:
    """Read the fresh archive or download it once for the local cache."""
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
        except OSError:
            return None


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
