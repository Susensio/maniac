"""Where a page comes from, in order: shipped, then upstream (CONTRACT.md).

The order is written here once. `attempts` walks it for `install` and `why`;
`scan` checks the same two sources with `shipped_attempt` and
`upstream_attempt`, on its own schedule (the upstream check runs in the
background, for rows nothing local answered). Callers keep their verdicts,
persistence and freshness policy (ADR-0040); generating a page is what is
left when no attempt finds one, and `seed_for` picks, in its own order, the
page people wrote for another version that generation starts from.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from ... import manifest
from ...config import Config
from ...exceptions import MalformedToolMetadata
from ...models import Installation, LocalRepoSource, RemoteRepoSource, RepoSource
from ..docs import discover_repo_manpages
from ..docs import redirect as redirects
from ..docs.pages import discovered_manpage_uri
from ..manpages import (
    find_installed_manpage_path,
    is_help2man_content,
    manpage_documents,
    read_manpage_source,
    select_primary_manpage,
)
from ..packages import ExternalPageFreshness, verify_external_page
from ..pathcache import resolve_cached
from ..providers.base import DirectPageProvider, Provider
from ..providers.registry import registry
from ..roff import header_version


@dataclass(frozen=True, slots=True)
class CandidatePage:
    """One validated materialized page and the exact URI that supplied it."""

    path: Path
    uri: str | None


@dataclass(frozen=True, slots=True)
class PageLink:
    """Where one shipped page's manpath link points, and whether that is the
    provider's own file (inside the install root) or needs maniac's copy."""

    target: Path
    provider_owned: bool


@dataclass(frozen=True, slots=True)
class InstallRootCandidate:
    """Local install-root evidence, with each page's link target.

    `links` is one per page, in `pages`' order: every page of a release --
    the primary and its companions alike -- links to the provider's file
    when that file is proven inside the install root, and is copied
    otherwise.
    """

    pages: tuple[CandidatePage, ...]
    primary: CandidatePage
    discovered_page: Path
    links: tuple[PageLink, ...]


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


def _link(provider: Provider, installation: Installation, page: Path) -> PageLink:
    """The provider's own target for `page` (a validated alias, ADR-0029, or
    the page itself), owned only when it resolves inside the install root."""
    target = page
    if isinstance(provider, DirectPageProvider):
        target = provider.direct_page_target(installation, page) or page
    return PageLink(target, _contained(target, installation.root))


def select_install_root(
    provider: Provider, installation: Installation
) -> InstallRootCandidate | None:
    """Select local evidence only; this never resolves a repository or network source."""
    found = provider.local_docs(installation)
    discovered = select_primary_manpage(found, installation.binary)
    if discovered is None:
        return None
    pages = tuple(CandidatePage(page, page.absolute().as_uri()) for page in found)
    primary = next(page for page in pages if page.path == discovered)
    return InstallRootCandidate(
        pages=pages,
        primary=primary,
        discovered_page=discovered,
        links=tuple(_link(provider, installation, page.path) for page in pages),
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


class Decided(Enum):
    """What chose the repository, when the installer's record did not alone."""

    CONFIG = "your config.toml"
    DETECTED = "detected"


@dataclass(frozen=True, slots=True)
class Repository:
    """The repository that documents an installation, or why it is unknown.

    `recorded` is the installer's own record and `decided` what overruled or
    confirmed it, both None when the record stood unexamined (ADR-0067).
    `evidence` is detection's, for `install`'s warning and `why`.
    `unfinished` says detection was asked and could not finish (rule 4).
    """

    source: RepoSource | None
    error: MalformedToolMetadata | None = None
    recorded: RepoSource | None = None
    decided: Decided | None = None
    evidence: str | None = None
    unfinished: bool = False

    @property
    def redirected(self) -> bool:
        return self.recorded is not None and self.recorded != self.source


def repository_of(
    provider: Provider, installation: Installation, config: Config
) -> Repository:
    """The repository whose documentation describes `installation`.

    The installer's own record names it (the registry); the user's
    `[documentation_repositories]` table, else the repository the record
    points at as the tool's own (`docs.redirect`), may replace it (ADR-0067).
    An installer record that cannot be read is returned, not raised: `scan`
    shows it on the tool's row.
    """
    try:
        raw = registry.resolve_source(installation, config=config, provider=provider)
    except MalformedToolMetadata as e:
        return Repository(None, e)
    if raw is None:
        return Repository(None)
    pinned = config.documentation_repository_overrides.get(raw.identity)
    if pinned is not None:
        source = (
            raw
            if pinned == raw.identity
            else RemoteRepoSource.from_identifier(raw.name, pinned)
        )
        return Repository(source, recorded=raw, decided=Decided.CONFIG)
    found, final = redirects.detect(
        raw,
        binary=installation.binary,
        package=installation.package,
        version=installation.version,
        cache_dir=config.cache_dir,
        config=config,
    )
    if found is None:
        return Repository(raw, unfinished=not final)
    return Repository(
        RemoteRepoSource.from_identifier(raw.name, found.target),
        recorded=raw,
        decided=Decided.DETECTED,
        evidence=found.evidence(raw),
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


SEED_MAX_CHARS = 40_000
"""A seed is context, not the answer: enough of a page to carry its structure."""


@dataclass(frozen=True, slots=True)
class Seed:
    """A page people wrote for another version of the same software.

    Generation reads it for structure, prose and examples `--help` lacks;
    the current `--help` wins wherever they disagree. `documents` is the
    version it was written for, None when nothing says.
    """

    path: Path
    content: str
    origin: str
    documents: str | None

    @property
    def label(self) -> str:
        """What the seed is, the way the page header and `why` name it."""
        version = self.documents or "version unknown"
        return f"{self.origin} ({version})"


def seed_for(
    tool: str,
    provider: Provider | None,
    installation: Installation | None,
    repository: Callable[[], RepoSource | None],
    *,
    config: Config,
    cache_dir: str | Path | None = None,
) -> Seed | None:
    """The page generation starts from, if one exists; first found wins.

    1. The page maniac installed before from the tool itself or its
       repository -- for this tool, so the same software, written by people.
    2. The page `man` shows today, when dpkg proves its package is the same
       software at another version.
    3. The repository's own page from its default branch or latest release:
       the installation's repository, so the same software by construction.

    Only pages people wrote: never one maniac generated, nor one help2man
    made from the same `--help` the model already gets. A shared name alone
    is no evidence, so a page nothing ties to this software is passed over.
    """
    entry = manifest.lookup(tool, config=config)
    if (
        entry is not None
        and entry.tier is not manifest.Tier.SYNTHESIS
        and entry.target is not None
    ):
        seed = _seed_from(entry.target, tool, "the page maniac installed before")
        if seed is not None:
            return replace(seed, documents=entry.version or seed.documents)

    if installation is not None and installation.version is not None:
        shown = find_installed_manpage_path("man", tool)
        if shown is not None:
            verdict = verify_external_page(
                shown, package=installation.package, version=installation.version
            )
            if verdict.freshness is ExternalPageFreshness.MISMATCH:
                seed = _seed_from(shown, tool, f"the {verdict.owner} package's page")
                if seed is not None:
                    return replace(seed, documents=verdict.version or seed.documents)

    if provider is None or installation is None:
        return None
    source = repository()
    if source is None or isinstance(source, LocalRepoSource):
        return None
    found, _ = discover_repo_manpages(
        source, installation.binary, cache_dir=cache_dir, config=config, version=None
    )
    if not found:
        return None
    return _seed_from(found[0], tool, f"the current page in {source.identity}")


def _seed_from(page: Path, tool: str, origin: str) -> Seed | None:
    """`page` as a seed, if people wrote it for `tool`; None otherwise."""
    try:
        content = read_manpage_source(page)
    except (OSError, UnicodeError, EOFError):
        return None
    if (
        content.startswith(manifest.PROVENANCE_SIGNATURE)
        or is_help2man_content(content)
        or not manpage_documents(content, tool)
    ):
        return None
    return Seed(page, content[:SEED_MAX_CHARS], origin, header_version(content))
