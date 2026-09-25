"""Resolve PATH binaries through the ordered installer-provider registry.

The one place that knows both how a binary is named and which providers exist.
Providers and `discovery.py` sit below it and import nothing from here, so
composition is carried by the registry passed down a call rather than by
module- or class-global state.
"""

import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from ..config import Config
from ..exceptions import MalformedToolMetadata
from ..models import Installation, RepoSource
from . import loginpath
from .pathcache import resolve_bin_path
from .providers.base import Provider
from .providers.registry import registry


def find_installation(
    binary_name: str, bin_dir: str | Path | None = None
) -> tuple[Provider, Installation] | None:
    """Return the provider and `Installation` a binary resolves to, if any.

    Shares `discover_repo`'s own bin-path resolution but stops at the
    `Installation` itself rather than resolving its source -- ADR-0016's
    tier 1 (install root) and tier 2 (repository, version matched) both
    need the install root and version directly, not only what
    `resolve_source` derives from them.
    """
    bin_path = resolve_bin_path(binary_name, bin_dir)
    return _detect_via_registry(bin_path) if bin_path is not None else None


def discover_repo(
    binary_name: str, bin_dir: str | Path | None = None, *, config: Config
) -> RepoSource | None:
    """Resolve a binary's upstream source from its provider-owned installation.

    None means unresolvable: no provider (ADR-0015) detected an
    installation behind this binary, so nothing installation-derived backs
    a source for it. There is no bare-name fallback -- ADR-0015 rules that
    "a tool with no provider is reported as unresolvable and nothing is
    generated for it", closing the gap where this used to return
    `RepoSource(name=binary, target=binary)`, reachable by tier-3 synthesis
    and liable to synthesize a page for a genuinely unresolved tool.
    """
    found = find_installation(binary_name, bin_dir=bin_dir)
    if found is None:
        return None
    provider, inst = found
    return registry.resolve_source(inst, config=config, provider=provider)


def enumerate_installations(
    on_start: Callable[[int], None] | None = None,
    on_scan: Callable[[], None] | None = None,
    on_error: Callable[[str, MalformedToolMetadata], None] | None = None,
) -> list[tuple[Provider, Installation]]:
    """Claim each first-PATH binary once, preserving PATH precedence.

    `status`'s enumeration (ADR-0016 Stage 7) inverts from scanning the
    manpath to this: a binary is a unit MANIAC can act on because some
    provider claims it, whether or not a manpage for it exists anywhere
    yet -- capability the manpath scan could never see, not a page. A name
    is resolved once, at its first `$PATH` occurrence, since that is the
    binary that actually runs when two providers claim the same name
    (ADR-0016's tie-break).

    Walks `loginpath.login_path().dirs` (ADR-0020) rather than the `$PATH`
    MANIAC inherited, so the answer describes the machine rather than the
    shell that happened to invoke this.

    `on_start`/`on_scan`, both `None` by default, split the work into two
    phases to instrument: `on_start` fires once with the candidate count,
    right after the directory scan and before the per-candidate
    `_detect_via_registry` loop that dominates the cost; `on_scan` fires
    once per candidate processed in that loop.

    A candidate whose provider raises `MalformedToolMetadata` (ADR-0060: its
    own metadata file is present but unreadable) is reported through
    `on_error` -- `(name, error)` -- and dropped from the result rather than
    aborting every other candidate's enumeration. A later `$PATH` shadow
    that also errors is silently dropped instead: it never wins regardless,
    and does not have its own row to report an error against.

    A later `$PATH` occurrence of a claimed name never wins, but a provider
    that claims it too is retained on the winner's `Installation.losers`
    rather than discarded -- diagnostics can then show when PATH hides a
    better-documented install, without this changing which one resolves.
    """
    seen: dict[str, Path] = {}
    shadowed: dict[str, list[Path]] = {}
    for entry in loginpath.login_path().dirs:
        try:
            children = list(os.scandir(entry))
        except OSError:
            continue
        for child in children:
            try:
                if not child.is_file() or not os.access(child.path, os.X_OK):
                    continue
            except OSError:
                continue
            if child.name in seen:
                shadowed.setdefault(child.name, []).append(Path(child.path))
            else:
                seen[child.name] = Path(child.path)

    if on_start is not None:
        on_start(len(seen))

    found: list[tuple[Provider, Installation]] = []
    for name, bin_path in seen.items():
        try:
            claim = _detect_via_registry(bin_path)
        except MalformedToolMetadata as e:
            if on_error is not None:
                on_error(name, e)
            if on_scan is not None:
                on_scan()
            continue
        if claim is not None:
            provider, inst = claim
            losers: list[Installation] = []
            for shadow_path in shadowed.get(name, ()):
                try:
                    shadow_claim = _detect_via_registry(shadow_path)
                except MalformedToolMetadata:
                    continue
                if shadow_claim is not None:
                    losers.append(shadow_claim[1])
            if losers:
                inst = replace(inst, losers=tuple(losers))
            found.append((provider, inst))
        if on_scan is not None:
            on_scan()
    return sorted(found, key=lambda item: item[1].binary)


def _detect_via_registry(bin_path: Path) -> tuple[Provider, Installation] | None:
    """Return the first registered provider that claims one PATH candidate."""
    for provider in registry.candidates_for(bin_path):
        inst = provider.detect(bin_path)
        if inst is not None:
            return provider, inst
    return None
