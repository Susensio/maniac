"""Local evidence alone: what `man`, the manifest and the install root say.

Nothing here reaches the network, the registry or a terminal. `man -w` is
checked first and the manifest only afterwards, to tell a MANIAC-owned page
apart from one `man` would resolve regardless -- a distro page, or one a
user hand-placed. A manifest entry whose file exists but which `man` does
not resolve is therefore not reported MANIAC-owned: ownership requires
reachability, not only a record (ADR-0018 reversing ADR-0016).
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from .. import manifest
from ..config import Config
from ..sources.candidates import select_install_root
from ..sources.crawler import get_version
from ..sources.manpages import (
    _opener_for,
    find_installed_manpage_path,
)
from ..sources.packages import ExternalPageFreshness, verify_external_page
from ..sources.pathcache import resolve_cached
from ..sources.providers.base import DirectPageProvider
from ..sources.roff import verify_page_header
from .models import ActionState, Candidate, LocalClassification, PageSource


@dataclass(frozen=True, slots=True)
class _PageEvidence:
    """What the manifest says about the page `man` resolved."""

    entry: manifest.Entry | None
    owned: bool
    provider_target_current: bool


def _strip_compression(path: Path) -> Path:
    """Strip a trailing compression suffix, if `manpages._opener_for` recognizes one.

    Derives the recognized suffixes by asking `_opener_for` itself rather
    than a second hardcoded list (`manpages.py:557-569`) that could drift
    from it: a suffix is compressed exactly when `_opener_for` picks a
    decompressing opener over the plain-`open` default.
    """
    if _opener_for(path) is not open:
        return path.with_suffix("")
    return path


def _same_page(entry_path: Path, installed_path: Path) -> bool:
    """Whether a manifest entry and a `man -w` result name the same page.

    `man -w` may return a compressed path where the manifest recorded an
    uncompressed one (or vice versa), and either side may be a symlink, so
    both are resolved (`pathcache.resolve_cached`) and stripped of a
    compression suffix before comparison.
    """
    return _strip_compression(resolve_cached(entry_path)) == _strip_compression(
        resolve_cached(installed_path)
    )


def _under_root(path: Path, root: Path) -> bool:
    """Whether a resolved page path sits under an installation's root."""
    try:
        return resolve_cached(path).is_relative_to(resolve_cached(root))
    except (OSError, ValueError):
        return False


def _managed_source(entry: manifest.Entry) -> PageSource:
    """Return the content provenance recorded for a reachable managed page."""
    return {
        manifest.Tier.INSTALL_ROOT: PageSource.VENDOR,
        manifest.Tier.REPOSITORY: PageSource.UPSTREAM,
        manifest.Tier.SYNTHESIS: PageSource.MANIAC,
    }[entry.tier]


def _installed_source(
    candidate: Candidate, installed: Path, evidence: _PageEvidence
) -> PageSource:
    """Return the provenance of a page that `man` resolves."""
    if evidence.owned:
        assert evidence.entry is not None
        return _managed_source(evidence.entry)
    inst = candidate.installation
    if inst is not None and _under_root(installed, inst.root):
        return PageSource.VENDOR
    return PageSource.SYSTEM


def _managed_page_state(
    candidate: Candidate, cfg: Config, evidence: _PageEvidence
) -> ActionState:
    """Return a managed page's state from positive version evidence only."""
    entry = evidence.entry
    assert entry is not None
    if entry.version is None:
        return ActionState.OK
    inst = candidate.installation
    current_version = (
        inst.version if inst is not None else get_version([candidate.tool], config=cfg)
    )
    if (
        current_version is not None
        and entry.version != current_version
        and not evidence.provider_target_current
    ):
        return ActionState.OUTDATED
    return ActionState.OK


def _external_page_state(
    installed: Path, candidate: Candidate
) -> tuple[ActionState, str | None]:
    """Return the verified state and provable owner for an external page.

    dpkg is tried first; only when it has nothing to say at all
    (`UNVERIFIED` -- no owner found, an owner found but not provably the
    same software, or not on a Debian system) does the page's own
    `.TH`/`.Dt` header get a chance to prove freshness instead (ADR-0026).
    Proving sameness through normalized `${Source}` yields `MATCH`/
    `MISMATCH`; failing to prove it yields `UNVERIFIED`, so the roff
    fallback runs for every unverified row. The roff path never overrides
    an actual dpkg `MATCH`/`MISMATCH`, and never sets `owning_package` --
    it proves freshness, not ownership.
    """
    inst = candidate.installation
    assert inst is not None
    verification = verify_external_page(
        installed, package=inst.package, version=inst.version
    )
    freshness = verification.freshness
    if freshness is ExternalPageFreshness.UNVERIFIED:
        freshness = verify_page_header(
            installed, binary_name=candidate.tool, version=inst.version
        )
    state = {
        ExternalPageFreshness.MATCH: ActionState.OK,
        ExternalPageFreshness.MISMATCH: ActionState.OUTDATED,
        ExternalPageFreshness.UNVERIFIED: ActionState.UNVERIFIED,
    }[freshness]
    return state, verification.owner


def _resolved_page_classification(
    candidate: Candidate,
    cfg: Config,
    installed: Path,
    evidence: _PageEvidence,
) -> LocalClassification:
    """Classify the page that `man` resolved for a binary."""
    source = _installed_source(candidate, installed, evidence)
    if evidence.owned:
        assert evidence.entry is not None
        return LocalClassification(
            _managed_page_state(candidate, cfg, evidence),
            source,
            True,
            installed,
            evidence.entry.source_uri,
        )
    claimed = candidate.provider is not None and candidate.installation is not None
    if claimed and source is PageSource.SYSTEM:
        state, owning_package = _external_page_state(installed, candidate)
        return LocalClassification(
            state,
            source,
            False,
            installed,
            owning_package=owning_package,
        )
    return LocalClassification(ActionState.OK, source, False, installed)


def _unresolved_page_classification(candidate: Candidate) -> LocalClassification:
    """Classify a binary for which `man` resolves no page."""
    provider, inst = candidate.provider, candidate.installation
    if provider is not None and inst is not None:
        source = select_install_root(provider, inst)
        if source is not None:
            return LocalClassification(
                ActionState.AVAILABLE,
                PageSource.VENDOR,
                False,
                source.primary.path,
            )
    return LocalClassification(ActionState.MISSING, PageSource.NONE, False, None)


def provider_target_freshness(candidate: Candidate, target: Path | None) -> bool | None:
    """Return a provider-specific target freshness verdict when one exists."""
    inst = candidate.installation
    if inst is None or target is None:
        return None
    if not isinstance(candidate.provider, DirectPageProvider):
        return None
    return candidate.provider.is_direct_page_target_current(inst, target)


def _owns_resolved_page(entry: manifest.Entry | None, installed: Path) -> bool:
    """Whether the manifest entry names the very page `man` resolved."""
    return (
        entry is not None
        and (entry.path.exists() or entry.path.is_symlink())
        and _same_page(entry.path, installed)
    )


def classify(
    candidate: Candidate,
    cfg: Config,
    entries: Mapping[str, manifest.Entry] | None = None,
) -> LocalClassification:
    """Decide one binary's state and page source by whether `man` resolves it (ADR-0026).

    `entries` is a bulk run's single immutable manifest snapshot; omitting
    it falls back to a per-tool `manifest.lookup` for a direct caller.
    """
    installed = find_installed_manpage_path("man", candidate.tool)
    entry = (
        entries.get(candidate.tool)
        if entries is not None
        else manifest.lookup(candidate.tool, config=cfg)
    )
    # Independent of every branch below: a manifest entry can drift from disk
    # (page deleted by hand, a durable dir cleaned, another installer
    # overwriting the link) whatever `man` currently resolves or doesn't.
    drift = entry is not None and manifest.link_state(entry) is manifest.Link.BROKEN
    freshness = provider_target_freshness(
        candidate, entry.target if entry is not None else None
    )
    direct_target = entry is not None and entry.provider_target

    if direct_target and freshness is False:
        assert entry is not None
        return LocalClassification(
            ActionState.OUTDATED,
            PageSource.VENDOR,
            True,
            entry.path,
            entry.source_uri,
            drift=drift,
        )
    if installed is None:
        return replace(_unresolved_page_classification(candidate), drift=drift)
    return replace(
        _resolved_page_classification(
            candidate,
            cfg,
            installed,
            _PageEvidence(
                entry=entry,
                owned=_owns_resolved_page(entry, installed),
                provider_target_current=direct_target and freshness is True,
            ),
        ),
        drift=drift,
    )
