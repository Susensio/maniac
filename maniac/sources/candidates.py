"""Verified source evidence shared by install and list.

This module selects and validates pages; callers keep their own tier order,
availability verdicts, persistence, and provider freshness policy (ADR-0040).
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..models import Installation, LocalRepoSource, RepoSource
from .docs import discover_repo_manpages
from .docs.pages import discovered_manpage_uri
from .manpages import manpage_documents, read_manpage_source, select_primary_manpage
from .pathcache import resolve_cached
from .providers.base import DirectPageProvider, Provider


@dataclass(frozen=True, slots=True)
class CandidatePage:
    """One validated materialized page and the exact URI that supplied it."""

    path: Path
    uri: str | None


@dataclass(frozen=True, slots=True)
class InstallRootCandidate:
    """Local install-root evidence, including the provider's chosen link target."""

    pages: tuple[CandidatePage, ...]
    primary: CandidatePage
    discovered_page: Path
    final_target: Path
    provider_owned: bool


@dataclass(frozen=True, slots=True)
class RepositoryCandidate:
    """Validated repository pages and explicit version-match evidence."""

    source: RepoSource
    pages: tuple[CandidatePage, ...]
    primary: CandidatePage
    version_matched: bool | None


def _contained(path: Path, root: Path) -> bool:
    try:
        resolve_cached(path).relative_to(resolve_cached(root))
    except (OSError, ValueError):
        return False
    return True


def select_install_root(
    provider: Provider, installation: Installation
) -> InstallRootCandidate | None:
    """Select local evidence only; this never resolves a repository or network source."""
    found = provider.local_docs(installation)
    discovered = select_primary_manpage(found, installation.binary)
    if discovered is None:
        return None
    final_target = discovered
    if isinstance(provider, DirectPageProvider):
        final_target = (
            provider.direct_page_target(installation, discovered) or discovered
        )
    pages = tuple(CandidatePage(page, page.absolute().as_uri()) for page in found)
    primary = next(page for page in pages if page.path == discovered)
    return InstallRootCandidate(
        pages=pages,
        primary=primary,
        discovered_page=discovered,
        final_target=final_target,
        provider_owned=_contained(final_target, installation.root),
    )


def select_repository(
    source: RepoSource,
    binary: str,
    *,
    cache_dir: str | Path | None = None,
    config: Config | None = None,
    version: str | None = None,
    discover: Callable[..., list[Path] | Path | None] | None = None,
    page_uri: Callable[[Path], str | None] | None = None,
) -> RepositoryCandidate | None:
    """Probe a repository explicitly, requiring positive remote version evidence."""
    if not isinstance(source, LocalRepoSource) and version is None:
        return None
    discover = discover or discover_repo_manpages
    page_uri = page_uri or discovered_manpage_uri
    result = discover(
        source, binary, cache_dir=cache_dir, config=config, version=version
    )
    found = [result] if isinstance(result, Path) else result or []
    if not found:
        return None
    # Discovery normally validated this already; retain the boundary check for
    # a materialized page supplied by another adapter.
    if found[0].exists() and not manpage_documents(
        read_manpage_source(found[0]), binary
    ):
        return None
    pages = tuple(
        CandidatePage(
            page,
            page.absolute().as_uri()
            if isinstance(source, LocalRepoSource)
            else page_uri(page),
        )
        for page in found
    )
    return RepositoryCandidate(
        source=source,
        pages=pages,
        primary=pages[0],
        version_matched=None if isinstance(source, LocalRepoSource) else True,
    )
