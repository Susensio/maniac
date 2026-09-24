"""System binaries dpkg owns, registered last (ADR-0015).

Detection is `dpkg-query -S` evidence, not a hardcoded layout: every other
provider recognises its own installer's directory shape and only this one
needs to ask a package manager whether it claims the path at all, so it
routes last and never shadows a user-level installer's claim on the same
`$PATH` entry.

Upstream identity comes only from a URL that is itself a repository on a
forge `discovery._clean_git_url` already follows (GitHub today). A website
homepage or a Debian `Vcs-*` field is never enough -- the former names no
repository, the latter names Debian's own packaging repository, not
upstream's.
"""

from pathlib import Path

from ...config import Config
from ...models import Installation, RemoteRepoSource, RepoSource
from .. import discovery, packages
from ..pathcache import resolve_cached
from .base import SourceResolver


class DebianProvider:
    """Claims a system binary dpkg owns; resolves it only as far as a
    forge repository URL, never by name.
    """

    name = "debian"

    def can_detect(self, real_path: Path) -> bool:
        """Cheap directory check first; the batched ownership query only
        runs once real_path is already a system-bin-dir candidate."""
        return (
            real_path.parent in packages._SYSTEM_BIN_DIRS
            and real_path in packages._debian_bin_owners()
        )

    def detect(self, bin_path: Path) -> Installation | None:
        resolved = resolve_cached(bin_path)
        package = packages._debian_bin_owners().get(resolved)
        if package is None:
            return None
        raw_version = packages._debian_version(package)
        version = (
            packages._debian_upstream_version(raw_version)
            if raw_version is not None
            else None
        )
        return Installation(
            binary=bin_path.name,
            bin_path=bin_path,
            real_path=resolved,
            provider=self.name,
            package=package,
            version=version,
            root=Path("/usr"),
        )

    def resolve_source(
        self, inst: Installation, *, config: Config, sources: SourceResolver
    ) -> RepoSource | None:
        url = packages._debian_copyright_source(
            inst.package
        ) or packages._debian_homepage(inst.package)
        if not url:
            return None
        cleaned = discovery._clean_git_url(url)
        if cleaned == url:
            return None
        return RemoteRepoSource.from_identifier(inst.binary, cleaned)

    def local_docs(self, inst: Installation) -> list[Path]:
        return []
