"""Where a page comes from, in order: shipped, then upstream (CONTRACT.md).

The order is written here once. `attempts` walks it for `install` and `why`;
`scan` checks the same two sources with `shipped_attempt` and
`upstream_attempt`, on its own schedule (the upstream check runs in the
background, for rows nothing local answered). Callers keep their verdicts,
persistence and freshness policy (ADR-0040); generating a page is what is
left when no attempt finds one.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..config import Config
from ..exceptions import MalformedToolMetadata
from ..models import Installation, LocalRepoSource, RepoSource
from .docs import discover_repo_manpages
from .docs.pages import discovered_manpage_uri
from .documentation import documentation_source
from .manpages import manpage_documents, read_manpage_source, select_primary_manpage
from .pathcache import resolve_cached
from .providers.base import DirectPageProvider, Provider
from .providers.registry import registry


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
    discover: Callable[..., tuple[list[Path] | Path | None, bool]] | None = None,
    page_uri: Callable[[Path], str | None] | None = None,
) -> tuple[RepositoryCandidate | None, bool]:
    """Probe a repository explicitly, requiring positive remote version evidence.

    The `bool` alongside the candidate is whether the probe was definitive --
    see `docs.discover_repo_manpage`. `None` candidate with `definitive=False`
    means the probe itself failed, not that the repository was checked and
    found lacking.
    """
    if not isinstance(source, LocalRepoSource) and version is None:
        return None, True
    discover = discover or discover_repo_manpages
    page_uri = page_uri or discovered_manpage_uri
    result, definitive = discover(
        source, binary, cache_dir=cache_dir, config=config, version=version
    )
    found = [result] if isinstance(result, Path) else result or []
    if not found:
        return None, definitive
    # Discovery normally validated this already; retain the boundary check for
    # a materialized page supplied by another adapter.
    if found[0].exists() and not manpage_documents(
        read_manpage_source(found[0]), binary
    ):
        return None, definitive
    pages = tuple(
        CandidatePage(
            page,
            page.absolute().as_uri()
            if isinstance(source, LocalRepoSource)
            else page_uri(page),
        )
        for page in found
    )
    return (
        RepositoryCandidate(
            source=source,
            pages=pages,
            primary=pages[0],
            version_matched=None if isinstance(source, LocalRepoSource) else True,
        ),
        definitive,
    )


class Source(Enum):
    """A place a page can come from, before generating one (CONTRACT.md)."""

    SHIPPED = "shipped"
    UPSTREAM = "upstream"


@dataclass(frozen=True, slots=True)
class Attempt:
    """One source tried for one installation.

    `found` is the page set it offers, or None. `definitive` is False when
    the check itself did not complete (network, git): then nothing is known,
    which is not the same as nothing found (CONTRACT.md rule 4). For
    upstream, `repository` is the repository asked, or None when none is
    known.
    """

    source: Source
    found: "InstallRootCandidate | RepositoryCandidate | None"
    definitive: bool = True
    repository: RepoSource | None = None


@dataclass(frozen=True, slots=True)
class Repository:
    """The repository that documents an installation, or why it is unknown."""

    source: RepoSource | None
    error: MalformedToolMetadata | None = None


def repository_of(
    provider: Provider, installation: Installation, config: Config
) -> Repository:
    """The repository whose documentation describes `installation`.

    The installer's own record names it (the registry), and the
    documentation-repository overrides redirect it. An installer record that
    cannot be read is returned, not raised: `scan` shows it on the tool's row.
    """
    try:
        raw = registry.resolve_source(installation, config=config, provider=provider)
    except MalformedToolMetadata as e:
        return Repository(None, e)
    if raw is None:
        return Repository(None)
    return Repository(
        documentation_source(raw, config.documentation_repository_overrides)
    )


def shipped_attempt(provider: Provider, installation: Installation) -> Attempt:
    """The page the tool installed itself, in its install root."""
    return Attempt(Source.SHIPPED, select_install_root(provider, installation))


def upstream_attempt(
    repository: RepoSource | None,
    installation: Installation,
    *,
    config: Config,
    cache_dir: str | Path | None = None,
    discover: Callable[..., tuple[list[Path] | Path | None, bool]] | None = None,
    page_uri: Callable[[Path], str | None] | None = None,
) -> Attempt:
    """The repository's page for exactly the installed version.

    No repository known, or no installed version to match a tag against, is
    a definitive miss: there is nothing to ask (ADR-0016).
    """
    if repository is None or installation.version is None:
        return Attempt(Source.UPSTREAM, None, repository=repository)
    found, definitive = select_repository(
        repository,
        installation.binary,
        cache_dir=cache_dir,
        config=config,
        version=installation.version,
        discover=discover,
        page_uri=page_uri,
    )
    return Attempt(Source.UPSTREAM, found, definitive, repository)


def attempts(
    provider: Provider | None,
    installation: Installation | None,
    repository: Callable[[], RepoSource | None],
    *,
    config: Config,
    cache_dir: str | Path | None = None,
) -> Iterator[Attempt]:
    """Each source in order, lazily: a caller stopping at the first page found
    never resolves the repository nor reaches the network.

    A binary no installer claims has neither source: nothing yields.
    """
    if provider is None or installation is None:
        return
    yield shipped_attempt(provider, installation)
    if installation.version is None:
        # No version to match a tag: nothing to ask, so the repository is
        # not even resolved (ADR-0016).
        yield Attempt(Source.UPSTREAM, None)
        return
    yield upstream_attempt(
        repository(), installation, config=config, cache_dir=cache_dir
    )
