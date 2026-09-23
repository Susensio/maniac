"""Verify external manpages against native package-manager facts."""

import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from functools import cache
from pathlib import Path

from ..models import RepoSource
from .docs.cache import canonical_github_repository_id

_GITHUB_URL = re.compile(r"^https?://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?$")


def _github_identity(url: str) -> str | None:
    """`owner/repo` from a GitHub repository URL, or `None`."""
    match = _GITHUB_URL.match(url)
    return match.group(1) if match else None


class ExternalPageFreshness(Enum):
    """Package-backed conclusion for a reachable page outside an install root."""

    MATCH = "match"
    MISMATCH = "mismatch"
    UNVERIFIED = "unverified"
    WRONG_OWNER = "wrong_owner"


@dataclass(frozen=True, slots=True)
class ExternalPageVerification:
    """Freshness verdict alongside the Debian package proven to own the page.

    `owner` is set whenever `_debian_owner` names one, independent of
    `freshness` -- a page can resolve to an owner `_same_software` cannot
    tie to the binary's own package (freshness stays `UNVERIFIED`, `owner`
    still set) just as easily as to no provable owner at all (`UNVERIFIED`,
    `owner` `None`). `WRONG_OWNER` fires only once `${Homepage}` and the
    resolved upstream resolve to two distinct canonical GitHub repository
    IDs (ADR-0055 phase 2); absent evidence on either side, or a lookup
    failure, still yields `UNVERIFIED`.
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


@cache
def _debian_owner(page: str) -> str | None:
    """One Debian package owning `page`, or `None` if that cannot be proven."""
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
    owners = [name.strip() for name in owner.split(",") if name.strip()]
    return owners[0] if len(owners) == 1 else None


@cache
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


@cache
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


@cache
def _debian_homepage(package: str) -> str | None:
    """Debian `${Homepage}` for `package`, or `None` if blank or unreadable."""
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Homepage}", package],
            capture_output=True,
            check=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    homepage = result.stdout.strip()
    return homepage or None


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


def _wrong_owner_disproof(owner: str, upstream: RepoSource | None) -> bool | None:
    """Whether `owner` and `upstream` prove different GitHub repositories.

    `None` when sameness cannot be decided either way: no upstream, no
    GitHub identity on either side, or a lookup failure (never fails a row
    on an unreachable network). `False` when both resolve to the same
    canonical ID, proving sameness. `True` only when both resolve and
    disagree -- the sole path to `WRONG_OWNER` (ADR-0055 phase 2).
    """
    if upstream is None or upstream.clone_url is None:
        return None
    upstream_identity = _github_identity(upstream.clone_url)
    if upstream_identity is None:
        return None
    homepage = _debian_homepage(owner)
    if homepage is None:
        return None
    owner_identity = _github_identity(homepage)
    if owner_identity is None:
        return None
    owner_id = canonical_github_repository_id(owner_identity)
    upstream_id = canonical_github_repository_id(upstream_identity)
    if owner_id is None or upstream_id is None:
        return None
    return owner_id != upstream_id


def verify_external_page(
    page: Path,
    *,
    package: str,
    version: str | None,
    upstream: RepoSource | None,
) -> ExternalPageVerification:
    """Return Debian-backed freshness, alongside the owner it was checked against."""
    if version is None:
        return ExternalPageVerification(ExternalPageFreshness.UNVERIFIED, None)
    owner = _debian_owner(str(page))
    if owner is None:
        return ExternalPageVerification(ExternalPageFreshness.UNVERIFIED, None)
    if not _same_software(owner, package):
        disproof = _wrong_owner_disproof(owner, upstream)
        if disproof is None:
            return ExternalPageVerification(ExternalPageFreshness.UNVERIFIED, owner)
        if disproof:
            return ExternalPageVerification(ExternalPageFreshness.WRONG_OWNER, owner)
    package_version = _debian_version(owner)
    if package_version is None:
        return ExternalPageVerification(ExternalPageFreshness.UNVERIFIED, owner)
    if _debian_upstream_version(package_version) == version:
        return ExternalPageVerification(ExternalPageFreshness.MATCH, owner)
    return ExternalPageVerification(ExternalPageFreshness.MISMATCH, owner)
