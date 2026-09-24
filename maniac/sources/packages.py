"""Verify external manpages against native package-manager facts."""

import os
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from functools import cache
from pathlib import Path


class ExternalPageFreshness(Enum):
    """Package-backed conclusion for a reachable page outside an install root."""

    MATCH = "match"
    MISMATCH = "mismatch"
    UNVERIFIED = "unverified"


@dataclass(frozen=True, slots=True)
class ExternalPageVerification:
    """Freshness verdict alongside the Debian package proven to own the page.

    `owner` is set whenever `_debian_owner` names one, independent of
    `freshness` -- a page can resolve to an owner `_same_software` cannot
    tie to the binary's own package (freshness stays `UNVERIFIED`, `owner`
    still set) just as easily as to no provable owner at all (`UNVERIFIED`,
    `owner` `None`).
    """

    freshness: ExternalPageFreshness
    owner: str | None


def _package_name(package: str) -> str:
    """Package identity without Debian's optional architecture qualifier."""
    name, separator, architecture = package.rpartition(":")
    if separator and re.fullmatch(r"[a-z0-9][a-z0-9-]*", architecture):
        return name
    return package


_SPLIT_PACKAGE_SUFFIXES = ("-minimal", "-dev", "-doc", "-common", "-bin", "-data")
_LANGUAGE_TEAM_PREFIXES = ("rust-", "node-", "haskell-", "ruby-")
_GOLANG_GITHUB_PREFIX = re.compile(r"^golang-github-[^-]+-")
# Only the two shapes ADR-0055 admits: a hyphen-separated trailing version
# (`gcc-13`, `-13.2`) and a dotted trailing version with no hyphen
# (`python3.12`). A bare trailing digit run with neither is a project's own
# name (`bzip2`, `libxml2`, `lz4`), not Debian versioning.
_TRAILING_VERSION = re.compile(r"-\d[\d.]*$|\d\.[\d.]*\d$")


def _normalize_debian_name(name: str) -> str:
    """Debian's mechanical naming, reversed toward the upstream project name.

    Exactly the rewrites ADR-0055 admits as evidence: a split-package
    suffix, a trailing interpreter/compiler version, and a language-team
    prefix. Nothing else -- this is not open-ended name manipulation.
    """
    for suffix in _SPLIT_PACKAGE_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    name = _TRAILING_VERSION.sub("", name) or name
    golang_match = _GOLANG_GITHUB_PREFIX.match(name)
    if golang_match:
        return name[golang_match.end() :]
    for prefix in _LANGUAGE_TEAM_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _debian_upstream_version(version: str) -> str:
    """Debian version without epoch and package revision."""
    _, separator, without_epoch = version.partition(":")
    normalized = without_epoch if separator else version
    upstream, separator, revision = normalized.rpartition("-")
    return upstream if separator and revision else normalized


# Debian only ever installs manpages under these roots; a user manpath entry
# (e.g. `~/.local/share/man`) is never dpkg-owned, so it is left out of the
# scan and simply falls back to the per-page query if ever queried.
_DEBIAN_MANPATH_ROOTS = ("/usr/share/man", "/usr/local/share/man", "/usr/local/man")
_DEBIAN_MANPATH_ROOT_PREFIXES = tuple(
    f"{root}/".encode() for root in _DEBIAN_MANPATH_ROOTS
)

_DPKG_ADMINDIR = Path("/var/lib/dpkg")
_DPKG_INFO_DIR = _DPKG_ADMINDIR / "info"
_DPKG_DIVERSIONS_FILE = _DPKG_ADMINDIR / "diversions"


def _one_owner(names: str) -> str | None:
    """Single owner from a comma-separated owner list, or `None` if ambiguous."""
    owners = [name.strip() for name in names.split(",") if name.strip()]
    return owners[0] if len(owners) == 1 else None


def _one_owner_of(names: set[str]) -> str | None:
    """Single owner from a set of owning packages, or `None` if ambiguous."""
    return next(iter(names)) if len(names) == 1 else None


def _diverted_paths() -> set[str]:
    """Every page path named on either side of a dpkg diversion.

    `/var/lib/dpkg/diversions` is plain text, three lines per record:
    diverted-from, diverted-to, diverting package (dpkg-divert(1)'s own
    format). Either of the first two lines can be a page path.
    """
    try:
        lines = _DPKG_DIVERSIONS_FILE.read_text().splitlines()
    except OSError:
        return set()
    record_count = len(lines) - len(lines) % 3
    diverted: set[str] = set()
    for start in range(0, record_count, 3):
        diverted.add(lines[start])
        diverted.add(lines[start + 1])
    return diverted


def _fetch_debian_owner_map() -> dict[str, str | None]:
    """Every page owned by a Debian package under a manpath root, read
    straight from dpkg's own per-package file lists
    (`/var/lib/dpkg/info/<package>.list`) instead of shelling out to
    `dpkg-query -S`.

    Measured on this machine: the equivalent `dpkg-query -S` glob call
    costs 4.5-6.3s (dpkg re-reads its whole database per invocation,
    regardless of pattern); scanning the ~2200 `.list` files directly
    costs under 2s. The list-file format is stable and documented by
    dpkg-query(1) itself ("this option requires the /var/lib/dpkg/info
    directory in a special way"); `dlocate` reads it the same way for
    the same reason. A page listed in more than one `.list` file keeps
    the current ambiguous-owner semantics (`None`, like a comma-separated
    `dpkg-query -S` answer). A diverted page (either side of a
    `/var/lib/dpkg/diversions` record) is left out of the map entirely,
    so `_debian_owner` falls back to the untouched per-page query for it.
    Falls back the same way when the info directory is missing, unreadable,
    or holds no `.list` files -- never guessed.
    """
    try:
        list_files = [
            entry.name
            for entry in os.scandir(_DPKG_INFO_DIR)
            if entry.name.endswith(".list")
        ]
    except OSError:
        return {}
    if not list_files:
        return {}
    diverted = _diverted_paths()
    owners: dict[str, set[str]] = {}
    for name in list_files:
        package = name.removesuffix(".list")
        try:
            with (_DPKG_INFO_DIR / name).open("rb") as handle:
                data = handle.read()
        except OSError:
            continue
        for line in data.split(b"\n"):
            if not line.startswith(_DEBIAN_MANPATH_ROOT_PREFIXES):
                continue
            path = line.decode(errors="surrogateescape")
            if path in diverted:
                continue
            owners.setdefault(path, set()).add(package)
    return {path: _one_owner_of(names) for path, names in owners.items()}


_owner_map_lock = threading.Lock()
_owner_map_cache: dict[str, str | None] | None = None


def _debian_owner_map() -> dict[str, str | None]:
    """The cached batch map, built by exactly one caller.

    `list` classifies pages from a thread pool, so the first several
    external pages can all reach a cache miss before any of them has
    stored a result -- `functools.cache` only locks its own bookkeeping,
    not the wrapped call, so concurrent misses each run their own
    `dpkg-query -S` regardless. Double-checked locking around a plain
    module global runs it exactly once; the rest block on the lock and
    then read the value the first caller stored.
    """
    global _owner_map_cache
    if _owner_map_cache is not None:
        return _owner_map_cache
    with _owner_map_lock:
        if _owner_map_cache is None:
            _owner_map_cache = _fetch_debian_owner_map()
        return _owner_map_cache


def _debian_owner_map_reset() -> None:
    """Test-only: forget the cached batch map."""
    global _owner_map_cache
    with _owner_map_lock:
        _owner_map_cache = None


def _debian_owner_uncached(page: str) -> str | None:
    """One Debian package owning `page`, queried directly (no batch map)."""
    try:
        result = subprocess.run(
            ["dpkg-query", "-S", "--", page],
            capture_output=True,
            check=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    owner, separator, _ = result.stdout.strip().partition(": ")
    if not separator:
        return None
    return _one_owner(owner)


@cache
def _debian_owner(page: str) -> str | None:
    """One Debian package owning `page`, or `None` if that cannot be proven.

    Consults the process-cached batch map first; a page the map did not
    cover (outside every manpath root, or excluded as diverted) falls back
    to the single-page query so every page still gets an answer.
    """
    owners = _debian_owner_map()
    if page in owners:
        return owners[page]
    return _debian_owner_uncached(page)


class _DedupedCache:
    """Like `functools.cache`, but a concurrent miss on the same key blocks
    on the first caller's query instead of running its own.

    `list` classifies rows from a thread pool; several rows can name the
    same package (a `${Version}` and a `${Source}` lookup for it, or two
    rows owned by the same package) and all reach a cache miss before the
    first caller has stored a result -- `functools.cache` only locks its
    own bookkeeping, not the wrapped call, so each miss would shell out.
    """

    def __init__(self, func: Callable[[str], str | None]) -> None:
        self._func = func
        self._cache: dict[str, str | None] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def __call__(self, key: str) -> str | None:
        if key in self._cache:
            return self._cache[key]
        with self._locks_guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            if key not in self._cache:
                self._cache[key] = self._func(key)
            return self._cache[key]

    def cache_clear(self) -> None:
        """Test-only: forget every cached result and its lock."""
        with self._locks_guard:
            self._cache.clear()
            self._locks.clear()


@_DedupedCache
def _debian_version(package: str) -> str | None:
    """Installed Debian package version, or `None` if it cannot be read."""
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", package],
            capture_output=True,
            check=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    version = result.stdout.strip()
    return version if result.returncode == 0 and version else None


@_DedupedCache
def _debian_source(package: str) -> str | None:
    """Debian source package for `package`, or `package` itself when the
    field is blank -- a package that is its own source leaves it empty."""
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Source}", package],
            capture_output=True,
            check=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    source = result.stdout.strip()
    return source or package


def _same_software(owner: str, package: str) -> bool:
    """Whether Debian's `owner` and the provider's `package` name the same
    software: exact match first, then `${Source}` normalized toward
    Debian's mechanical naming (ADR-0055)."""
    owner_name = _package_name(owner)
    package_name = _package_name(package)
    if owner_name == package_name:
        return True
    source = _debian_source(owner_name)
    if source is None:
        return False
    return _normalize_debian_name(source) == package_name


def verify_external_page(
    page: Path,
    *,
    package: str,
    version: str | None,
) -> ExternalPageVerification:
    """Return Debian-backed freshness, alongside the owner it was checked against."""
    if version is None:
        return ExternalPageVerification(ExternalPageFreshness.UNVERIFIED, None)
    owner = _debian_owner(str(page))
    if owner is None:
        return ExternalPageVerification(ExternalPageFreshness.UNVERIFIED, None)
    if not _same_software(owner, package):
        return ExternalPageVerification(ExternalPageFreshness.UNVERIFIED, owner)
    package_version = _debian_version(owner)
    if package_version is None:
        return ExternalPageVerification(ExternalPageFreshness.UNVERIFIED, owner)
    if _debian_upstream_version(package_version) == version:
        return ExternalPageVerification(ExternalPageFreshness.MATCH, owner)
    return ExternalPageVerification(ExternalPageFreshness.MISMATCH, owner)
