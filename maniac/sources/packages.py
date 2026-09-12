"""Verify external manpages against native package-manager facts."""

import re
import subprocess
from enum import Enum
from functools import cache
from pathlib import Path


class ExternalPageFreshness(Enum):
    """Package-backed conclusion for a reachable page outside an install root."""

    MATCH = "match"
    MISMATCH = "mismatch"
    UNVERIFIED = "unverified"


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
) -> ExternalPageFreshness:
    """Return Debian-backed freshness, otherwise an explicitly unknown result."""
    if version is None:
        return ExternalPageFreshness.UNVERIFIED
    owner = _debian_owner(str(page))
    if owner is None or _package_name(owner) != _package_name(package):
        return ExternalPageFreshness.UNVERIFIED
    package_version = _debian_version(owner)
    if package_version is None:
        return ExternalPageFreshness.UNVERIFIED
    if _debian_upstream_version(package_version) == version:
        return ExternalPageFreshness.MATCH
    return ExternalPageFreshness.MISMATCH
