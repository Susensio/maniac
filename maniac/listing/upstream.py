"""Repository identity and the version-pinned tier-2 page probe.

ADR-0025 orders these behind local evidence: identity is resolved only for a
row local classification left open, and the remote probe runs only when that
row also carries a version. An `ok` row and an install-root `available` row
are already final, so neither pays registry, provider or network work.
"""

from pathlib import Path

from ..config import Config
from ..logging import logger
from ..models import Installation, RepoSource
from ..sources.docs import discover_repo_manpage, discovered_manpage_uri
from ..sources.documentation import documentation_source
from ..sources.providers.registry import registry
from .models import ActionState, Candidate, LocalClassification, PageSource, ToolRow

ProbeKey = tuple[str, str, str]
"""Clone target, installed version and binary name: one probe's identity."""

ProbePage = tuple[Path, str | None]
"""A materialized upstream page and the remote URI it came from, if any."""


def resolve_upstream(candidate: Candidate, *, config: Config) -> RepoSource | None:
    """Resolve the candidate installation's upstream repository."""
    provider, inst = candidate.provider, candidate.installation
    if provider is None or inst is None:
        return None
    source = registry.resolve_source(inst, config=config, provider=provider)
    return (
        documentation_source(source, config.documentation_repository_overrides)
        if source is not None
        else None
    )


def _unresolved_locally(
    state: ActionState, source: PageSource, page_uri: str | None
) -> bool:
    """Whether local evidence left an upstream question open for this row.

    Two shapes qualify: nothing resolved at all, and a repository-tier page
    that resolved without the remote URI its Source link needs (ADR-0027's
    recovery path for manifest entries written before that field existed).
    """
    return (state is ActionState.MISSING and source is PageSource.NONE) or (
        source is PageSource.UPSTREAM and page_uri is None
    )


def needs_upstream_identity(classified: LocalClassification) -> bool:
    """Whether ADR-0025 lets this row pay for repository resolution at all."""
    return _unresolved_locally(classified.state, classified.source, classified.page_uri)


def is_upstream_eligible(row: ToolRow, inst: Installation | None) -> bool:
    """Whether one completed local row needs the version-matched remote check."""
    return (
        _unresolved_locally(row.state, row.source, row.page_uri)
        and row.upstream is not None
        and inst is not None
        and inst.version is not None
    )


def probe_key(source: RepoSource, inst: Installation) -> ProbeKey:
    """Identity of one version-pinned availability probe."""
    return (source.clone_url or source.target, inst.version or "", inst.binary)


def probe_upstream(
    source: RepoSource, inst: Installation, cfg: Config
) -> ProbePage | None:
    """Return tier 2's version-matched manpage for one unresolved row."""
    try:
        page = discover_repo_manpage(
            source,
            inst.binary,
            cache_dir=cfg.cache_dir,
            config=cfg,
            version=inst.version,
        )
        if page is None:
            return None
        uri = (
            page.absolute().as_uri()
            if source.is_local
            else discovered_manpage_uri(page)
        )
        return page, uri
    except (OSError, UnicodeError) as error:
        # Repository probing is supplementary to the local reachability result.
        logger.debug(
            "Error probing upstream manpage",
            tool=inst.binary,
            source=source.target,
            error=str(error),
        )
        return None
