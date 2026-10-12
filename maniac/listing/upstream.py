"""`scan`'s version-pinned upstream page probe.

Repository identity (`sources.candidates.repository_of`) is resolved for every
row: it is a cached registry read plus a dict lookup, and the Upstream column
names where each tool comes from regardless of how its page was found.
ADR-0025's ordering still governs the remote probe, which is per-row network
work and runs only for a row local classification left open that also carries
a version.
"""

from pathlib import Path

from ..config import Config
from ..logging import logger
from ..models import Installation, RepoSource
from ..sources.candidates import upstream_attempt
from ..sources.docs import discover_repo_manpage
from ..sources.docs.pages import discovered_manpage_uri
from .models import ActionState, PageSource, ToolRow

ProbeKey = tuple[str, str, str]
"""Clone target, installed version and binary name: one probe's identity."""

ProbePage = tuple[Path, str | None]
"""A materialized upstream page and the remote URI it came from, if any."""


def is_upstream_eligible(row: ToolRow, inst: Installation | None) -> bool:
    """Whether one completed local row needs the version-matched remote check.

    Two row shapes leave an upstream question open: nothing resolved at all,
    and a repository-tier page that resolved without the remote URI its Source
    link needs (ADR-0027's recovery path for manifest entries written before
    that field existed).
    """
    unresolved_locally = (
        row.state is ActionState.MISSING and row.source is PageSource.NONE
    ) or (row.source is PageSource.UPSTREAM and row.page_uri is None)
    return (
        unresolved_locally
        and row.upstream is not None
        and inst is not None
        and inst.version is not None
    )


def probe_key(source: RepoSource, inst: Installation) -> ProbeKey:
    """Identity of one version-pinned availability probe."""
    return (source.clone_url or source.identity, inst.version or "", inst.binary)


def probe_upstream(
    source: RepoSource, inst: Installation, cfg: Config
) -> ProbePage | None:
    """Return tier 2's version-matched manpage for one unresolved row."""
    try:
        attempt = upstream_attempt(
            source,
            inst,
            config=cfg,
            cache_dir=cfg.cache_dir,
            # Only the primary page: a row needs to know one exists, not the
            # whole release `install` will fetch.
            discover=discover_repo_manpage,
            page_uri=discovered_manpage_uri,
        )
        candidate = attempt.found
        if candidate is None:
            if not attempt.definitive:
                # A listing row is advisory, not a refusal point (ADR-0016's
                # refuse-on-non-definitive applies to install's synthesis
                # fallback, not this display) -- note it and move on.
                logger.debug(
                    "Tier-2 repository check did not complete",
                    tool=inst.binary,
                    source=source.identity,
                )
            return None
        return candidate.primary.path, candidate.primary.uri
    except (OSError, UnicodeError) as error:
        # Repository probing is supplementary to the local reachability result.
        logger.debug(
            "Error probing upstream manpage",
            tool=inst.binary,
            source=source.identity,
            error=str(error),
        )
        return None
