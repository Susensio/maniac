"""Verify external manpages against native package-manager facts."""

import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from functools import cache
from pathlib import Path


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
    `freshness` -- a page can resolve to a provable owner that is not the
    binary's own package (freshness becomes `WRONG_OWNER`) just as easily
    as to no provable owner at all (freshness stays `UNVERIFIED`).
    """

    freshness: ExternalPageFreshness
    owner: str | None


def _package_name(package: str) -> str:
    """Package identity without Debian's optional architecture qualifier."""
    name, separator, architecture = package.rpartition(":")
    if separator and re.fullmatch(r"[a-z0-9][a-z0-9-]*", architecture):
        return name
    return package


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


def verify_external_page(
    page: Path, *, package: str, version: str | None
) -> ExternalPageVerification:
    """Return Debian-backed freshness, alongside the owner it was checked against."""
    if version is None:
        return ExternalPageVerification(ExternalPageFreshness.UNVERIFIED, None)
    owner = _debian_owner(str(page))
    if owner is None:
        return ExternalPageVerification(ExternalPageFreshness.UNVERIFIED, None)
    if _package_name(owner) != _package_name(package):
        return ExternalPageVerification(ExternalPageFreshness.WRONG_OWNER, owner)
    package_version = _debian_version(owner)
    if package_version is None:
        return ExternalPageVerification(ExternalPageFreshness.UNVERIFIED, owner)
    if _debian_upstream_version(package_version) == version:
        return ExternalPageVerification(ExternalPageFreshness.MATCH, owner)
    return ExternalPageVerification(ExternalPageFreshness.MISMATCH, owner)
