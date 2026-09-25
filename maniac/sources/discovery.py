"""Mise registry and configuration lookups behind provider source resolution.

Deliberately depends on no provider and no resolver: `providers/mise.py` and
the URL cleaning its peers share both import this, so anything here reaching
back up would close a cycle. Resolving a binary to a provider lives in
`resolution.py`; naming a binary at all lives in `pathcache.py`.
"""

import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import tomllib
from functools import cache
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

# Guards against `mise` hanging rather than the ordinary ~170ms cost
# (matches `providers/mise.py`'s own query timeout, ADR-0061).
_MISE_QUERY_TIMEOUT = 5


class _MiseRegistryLoader:
    """Process-local, per-cache-path single-flight registry loader.

    A load that fails is memoized too (`_failure`), not only a load that
    succeeds -- offline plus a corrupt cache otherwise retried `urlopen`
    (10s timeout) once per mise tool looked up in the same process
    (confirmed: 5 lookups, 5 attempts).
    """

    def __init__(self) -> None:
        self._cache: dict[Path, dict[str, str]] = {}
        self._failure: dict[Path, MalformedToolMetadata] = {}
        self._locks: dict[Path, threading.Lock] = {}
        self._guard = threading.Lock()

    def __call__(self, cache_path: Path) -> dict[str, str]:
        with self._guard:
            cached = self._cache.get(cache_path)
            if cached is not None:
                return cached
            failed = self._failure.get(cache_path)
            if failed is not None:
                raise failed
            lock = self._locks.setdefault(cache_path, threading.Lock())

        with lock:
            with self._guard:
                cached = self._cache.get(cache_path)
                if cached is not None:
                    return cached
                failed = self._failure.get(cache_path)
                if failed is not None:
                    raise failed

            try:
                registry = _load_mise_registry_uncached(cache_path)
            except MalformedToolMetadata as e:
                with self._guard:
                    self._failure[cache_path] = e
                raise
            with self._guard:
                self._cache[cache_path] = registry
            return registry

    def cache_clear(self) -> None:
        with self._guard:
            self._cache.clear()
            self._failure.clear()


def _run_mise(*args: str) -> str:
    """Run one `mise` subcommand from `$HOME` and return its stdout.

    Every `MISE_*`/`__MISE_*` variable is scrubbed first -- `MISE_CONFIG_FILE`
    alone was measured to leak a project's config into a `$HOME` query
    (ADR-0061), same reasoning as `providers/mise.py`'s
    `_mise_global_install_identities`. A mise-managed install already found
    under mise's own tree is evidence mise itself should be reachable and
    working here, so every failure mode raises rather than being treated as
    ADR-0060 absence.
    """
    mise = shutil.which("mise")
    if mise is None:
        raise MalformedToolMetadata(
            Path("mise"),
            "mise binary not found on $PATH, but a mise-managed install exists",
        )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("MISE_", "__MISE_"))
    }
    try:
        result = subprocess.run(
            [mise, *args],
            cwd=Path.home(),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=_MISE_QUERY_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise MalformedToolMetadata(
            Path(mise), f"mise {' '.join(args)} failed: {e}"
        ) from e
    if result.returncode != 0:
        raise MalformedToolMetadata(
            Path(mise), f"mise {' '.join(args)} exited {result.returncode}"
        )
    return result.stdout


@cache
def _mise_config_files() -> tuple[Path, ...]:
    """Config files mise itself tracks, from `mise config ls --json` at `$HOME`.

    Mise's own answer, not a directory glob (ADR-0060's test): a stray
    `.toml` under mise's config directory that mise itself ignores must not
    turn into a broken read for every mise tool that falls back to config
    inspection. Cached for the life of the process, same as
    `_mise_global_install_identities`.
    """
    try:
        data = json.loads(_run_mise("config", "ls", "--json"))
    except json.JSONDecodeError as e:
        raise MalformedToolMetadata(
            Path("mise"), f"unparseable mise config ls --json output: {e}"
        ) from e
    if not isinstance(data, list) or not all(
        isinstance(entry, dict) and isinstance(entry.get("path"), str) for entry in data
    ):
        raise MalformedToolMetadata(
            Path("mise"), "unexpected mise config ls --json shape"
        )
    return tuple(Path(entry["path"]) for entry in data)


@cache
def _mise_config_data(cfg_path: Path) -> dict[str, object]:
    """Mise's own resolved view of one tracked config file (`mise config get -f`).

    Cached per path for the life of the process: `_resolve_from_mise` calls
    this once per mise-config-fallback tool lookup, and every lookup in one
    run shares the same tracked files.
    """
    try:
        return tomllib.loads(_run_mise("config", "get", "-f", str(cfg_path)))
    except tomllib.TOMLDecodeError as e:
        raise MalformedToolMetadata(cfg_path, str(e)) from e


def _check_mise_config(cfg_path: Path, tool_id: str, binary_name: str) -> str | None:
    """Inspect mise's own answer for one tracked config file's tool aliases
    or tool repository definitions (`_mise_config_data`, ADR-0060)."""
    data = _mise_config_data(cfg_path)

    aliases = data.get("tool_alias", {})
    if not isinstance(aliases, dict):
        raise MalformedToolMetadata(cfg_path, "tool_alias is not a table")
    for alias_name, alias_target in aliases.items():
        if alias_name not in (tool_id, binary_name):
            continue
        if not isinstance(alias_target, str):
            raise MalformedToolMetadata(
                cfg_path, f"tool_alias.{alias_name} is not a string"
            )
        if alias_target.startswith("github:"):
            return alias_target.split(":", 1)[1]
        return alias_target

    tools = data.get("tools", {})
    if not isinstance(tools, dict):
        raise MalformedToolMetadata(cfg_path, "tools is not a table")
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
    """Infer a repository from Mise's own configuration, then its registry.

    `tool_id` is looked up as-is -- no fallback to `binary_name` in the
    registry query. Both must be installation-derived (ADR-0008): the caller
    is `MiseProvider.resolve_source`, keying on the install directory name
    it detected, never a bare command-line name with no installation behind
    it (ADR-0015's ruling on Stage 2's gap).

    `offline=True` skips `_query_mise_registry`, the one step here that can
    reach the network (`_read_mise_registry_archive`'s `urlopen`, on a cache
    miss or a stale TTL). Mise config resolution is a local subprocess call
    to `mise` itself and is attempted in either mode; callers that leave it
    false permit the registry fallback and its cache refresh.
    """
    for cfg_path in _mise_config_files():
        found = _check_mise_config(cfg_path, tool_id, binary_name)
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


_MISE_REGISTRY_PARSE_ERRORS = (
    OSError,
    tarfile.TarError,
    tomllib.TOMLDecodeError,
    UnicodeDecodeError,
    zstandard.ZstdError,
)


def _load_mise_registry_uncached(cache_path: Path) -> dict[str, str]:
    """Load short names, aliases, and bins from Mise's cached registry archive.

    A cache file `_read_mise_registry_archive` returned as fine to read can
    still hold content that will not parse -- the read only checks the file
    is reachable, not that it is a valid zstd-compressed tar. That is
    maniac's own disposable state too (ADR-0060 Corrections): deleted and
    rebuilt once here; only a rebuild that also fails to parse is reported.
    """
    archive = _read_mise_registry_archive(cache_path)
    if archive is None:
        return {}

    try:
        return _parse_mise_registry(archive)
    except _MISE_REGISTRY_PARSE_ERRORS as e:
        cache_path.unlink(missing_ok=True)
        rebuilt = _read_mise_registry_archive(cache_path)
        if rebuilt is None:
            raise MalformedToolMetadata(
                cache_path, f"cache was corrupt and could not be rebuilt: {e}"
            ) from e
        try:
            return _parse_mise_registry(rebuilt)
        except _MISE_REGISTRY_PARSE_ERRORS as e2:
            raise MalformedToolMetadata(cache_path, str(e2)) from e2


_load_mise_registry = _MiseRegistryLoader()


def _read_mise_registry_archive(cache_path: Path) -> bytes | None:
    """Read the fresh archive from cache, or download it once for the local cache.

    A cache file that vanishes mid-check (`FileNotFoundError`) is an
    ordinary race with a concurrent write, treated the same as never having
    had one. Any other read failure -- permissions, an I/O error -- means
    maniac's own cache is corrupt or unreadable. This is maniac's own
    disposable state, not the user's environment (ADR-0060 Corrections): it
    is deleted and rebuilt below rather than reported immediately, and only
    a rebuild that itself fails (e.g. offline) surfaces as an error.
    """
    corrupt = False
    try:
        fresh = (
            cache_path.is_file()
            and time() - cache_path.stat().st_mtime < MISE_REGISTRY_TTL_SECONDS
        )
    except FileNotFoundError:
        fresh = False
    except OSError:
        corrupt = True
        fresh = False
    if fresh:
        try:
            return cache_path.read_bytes()
        except FileNotFoundError:
            pass
        except OSError:
            corrupt = True

    if corrupt:
        try:
            cache_path.unlink(missing_ok=True)
        except OSError:
            # Best-effort: a directory this broken (e.g. unreadable itself)
            # fails the rebuild attempt below the same way, which is what
            # decides whether this surfaces.
            pass

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
            if corrupt:
                raise MalformedToolMetadata(
                    cache_path, f"cache was corrupt and could not be rebuilt: {e}"
                ) from e
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
