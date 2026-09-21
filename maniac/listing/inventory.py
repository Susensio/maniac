"""Enumerate candidates, classify them locally, probe upstream, publish snapshots.

This is `list`'s whole decision surface and it knows nothing about a
terminal. An `InventoryObserver` receives immutable, ordered snapshots
(ADR-0024) and can therefore watch the run without steering it: the
coordinator thread alone mutates rows, and every published view is a copy.

Local `man -w` work runs in one bounded pool; each locally unresolved row's
version-pinned tier-2 probe enters a second bounded pool as soon as that row
is ready, deduplicated by probe identity so siblings finalize atomically
(ADR-0025).
"""

import hashlib
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from functools import cache
from itertools import count
from pathlib import Path
from time import monotonic
from types import MappingProxyType
from typing import Any

from .. import manifest
from ..config import Config
from ..logging import logger
from ..models import Installation, RepoSource
from ..sources import resolution
from .classification import classify
from .models import (
    ActionState,
    Candidate,
    LocalClassification,
    PageSource,
    RowSnapshot,
    ToolRow,
)
from .upstream import (
    ProbeKey,
    ProbePage,
    is_upstream_eligible,
    probe_key,
    probe_upstream,
    resolve_upstream,
)

LOCAL_CLASSIFY_WORKERS = 8
UPSTREAM_PROBE_WORKERS = 8

_LocalResult = tuple[LocalClassification, RepoSource | None]
_Launch = tuple[ProbeKey, RepoSource, Installation]


class InventoryObserver:
    """No-op sink for one run's progress and row snapshots; renderers subclass it.

    Every snapshot is an immutable tuple taken by the coordinator, so an
    observer can neither reach worker state nor change what a later row is
    classified as.
    """

    def discovery_started(self, total: int) -> None:
        """A provider-walk phase began with `total` candidates to scan."""

    def discovery_scanned(self) -> None:
        """One discovery candidate was scanned."""

    def rows_started(self, total: int) -> None:
        """Classification began for `total` rows."""

    def row_scanned(self) -> None:
        """One row reached its final state."""

    def inventory_ready(self, rows: RowSnapshot) -> None:
        """The complete alphabetized skeleton exists, before any classification."""

    def row_classified(
        self, rows: RowSnapshot, index: int, upstream_pending: bool
    ) -> None:
        """One row's local facts landed; `upstream_pending` says a probe still runs."""

    def local_facts_ready(self, rows: RowSnapshot, upstream_pending: set[int]) -> None:
        """Every local classification finished, before any probe could change it."""

    def upstream_group_ready(self, rows: RowSnapshot, indexes: set[int]) -> None:
        """One deduplicated probe group finalized, all its rows at once."""

    def idle(self) -> None:
        """Workers are quiet while futures remain pending."""


def _build_inventory(
    tools: list[str] | None, observer: InventoryObserver
) -> tuple[list[Candidate], bool]:
    """Return requested or discovered candidates and whether discovery ran."""
    if tools:
        # No `resolution.discover_repo(tool)` fallback when `found` is None:
        # it shares `find_installation`'s own bin-path resolution.
        candidates = []
        for tool in dict.fromkeys(tools):
            found = resolution.find_installation(tool)
            provider, inst = found if found else (None, None)
            candidates.append(
                Candidate(tool=tool, provider=provider, installation=inst)
            )
        return candidates, False

    discovered = sorted(
        resolution.enumerate_installations(
            on_start=observer.discovery_started,
            on_scan=observer.discovery_scanned,
        ),
        key=lambda item: item[1].binary,
    )
    return [
        Candidate(tool=inst.binary, provider=provider, installation=inst)
        for provider, inst in discovered
    ], True


def _skeleton_row(candidate: Candidate) -> ToolRow:
    """One stable row before any classification has run."""
    return ToolRow(
        tool=candidate.tool,
        package=candidate.package,
        provider=candidate.provider_name,
        state=ActionState.MISSING,
        source=PageSource.NONE,
        upstream=None,
    )


def _classified_row(
    candidate: Candidate, classified: LocalClassification, upstream: RepoSource | None
) -> ToolRow:
    """Apply local classification and repository identity to one skeleton row."""
    return ToolRow(
        tool=candidate.tool,
        package=candidate.package,
        provider=candidate.provider_name,
        state=classified.state,
        source=classified.source,
        upstream=upstream,
        managed=classified.managed,
        page_path=classified.page_path,
        page_uri=classified.page_uri,
        owning_package=classified.owning_package,
        drift=classified.drift,
    )


def _classify_and_resolve(
    candidate: Candidate, cfg: Config, entries: Mapping[str, manifest.Entry]
) -> _LocalResult:
    """One row's local classification paired with its repository identity.

    Identity is resolved for every row; only the remote page probe that
    follows it stays gated on local evidence (`is_upstream_eligible`).
    """
    return classify(candidate, cfg, entries), resolve_upstream(candidate, config=cfg)


def _with_probe_result(row: ToolRow, page: ProbePage | None) -> ToolRow:
    """Apply one completed upstream page to an eligible row."""
    if page is None:
        return row
    path, uri = page
    if row.state is ActionState.MISSING and row.source is PageSource.NONE:
        return replace(
            row,
            state=ActionState.AVAILABLE,
            source=PageSource.UPSTREAM,
            page_path=path,
            page_uri=uri,
        )
    if row.source is PageSource.UPSTREAM:
        return replace(row, page_uri=uri)
    return row


@dataclass(slots=True)
class _RowCoordinator:
    """Own coordinator-side row state while local and upstream work overlap."""

    candidates: list[Candidate]
    rows: list[ToolRow]
    local_rows: list[ToolRow]
    observer: InventoryObserver
    probe_indexes: dict[ProbeKey, list[int]] = field(default_factory=dict)
    probe_results: dict[ProbeKey, ProbePage | None] = field(default_factory=dict)
    active_probe_keys: set[ProbeKey] = field(default_factory=set)
    local_eligible: set[int] = field(default_factory=set)

    def complete_local(
        self, index: int, classified: LocalClassification, upstream: RepoSource | None
    ) -> _Launch | None:
        """Record one local result and return its new upstream probe, if any."""
        candidate = self.candidates[index]
        row = _classified_row(candidate, classified, upstream)
        self.rows[index] = row
        self.local_rows[index] = row
        pending_probe = is_upstream_eligible(row, candidate.installation)
        launch = None
        if pending_probe:
            launch, pending_probe = self._enrol_probe(index, row)
        else:
            self.observer.row_scanned()
        self.observer.row_classified(tuple(self.rows), index, pending_probe)
        return launch

    def _enrol_probe(self, index: int, row: ToolRow) -> tuple[_Launch | None, bool]:
        """Join one eligible row to its probe group; say whether it still waits."""
        inst = self.candidates[index].installation
        assert row.upstream is not None
        assert inst is not None
        key = probe_key(row.upstream, inst)
        self.probe_indexes.setdefault(key, []).append(index)
        self.local_eligible.add(index)
        if key in self.probe_results:
            self._publish_probe([index], self.probe_results[key])
            return None, False
        if key in self.active_probe_keys:
            return None, True
        self.active_probe_keys.add(key)
        return (key, row.upstream, inst), True

    def complete_probe(self, key: ProbeKey, page: ProbePage | None) -> None:
        """Record one completed probe and publish all rows sharing its key."""
        self.probe_results[key] = page
        self._publish_probe(self.probe_indexes[key], page)

    def _publish_probe(self, indexes: list[int], page: ProbePage | None) -> None:
        """Publish a completed deduplicated probe group atomically."""
        for index in indexes:
            self.rows[index] = _with_probe_result(self.rows[index], page)
        for _ in indexes:
            self.observer.row_scanned()
        self.observer.upstream_group_ready(tuple(self.rows), set(indexes))

    def publish_local_facts(self) -> None:
        """Publish the local-only snapshot, which no probe result can change."""
        self.observer.local_facts_ready(
            tuple(self.local_rows), set(self.local_eligible)
        )


def _next_completed_futures(
    local_pending: Mapping[Future[Any], int],
    probe_pending: Mapping[Future[Any], ProbeKey],
    observer: InventoryObserver,
) -> set[Future[Any]] | None:
    """Wait for work, notifying the observer when the workers are quiet."""
    done, _ = wait(
        [*local_pending, *probe_pending],
        timeout=0.25,
        return_when=FIRST_COMPLETED,
    )
    if done:
        return done
    observer.idle()
    return None


def _complete_local_futures(
    done: set[Future[Any]],
    local_pending: dict[Future[_LocalResult], int],
    coordinator: _RowCoordinator,
) -> list[_Launch]:
    """Record finished local work in stable row order and collect new probes."""
    launches = []
    for future in sorted(
        (future for future in done if future in local_pending),
        key=local_pending.__getitem__,
    ):
        index = local_pending.pop(future)
        classified, upstream = future.result()
        launch = coordinator.complete_local(index, classified, upstream)
        if launch is not None:
            launches.append(launch)
    return launches


def _complete_probe_futures(
    done: set[Future[Any]],
    probe_pending: dict[Future[ProbePage | None], ProbeKey],
    coordinator: _RowCoordinator,
) -> None:
    """Publish every already-completed upstream probe after local coordination."""
    for future in [future for future in done if future in probe_pending]:
        key = probe_pending.pop(future)
        coordinator.complete_probe(key, future.result())


@cache
def _content_digest(path: Path) -> str | None:
    """SHA-256 of a resolved file's bytes, or `None` if it cannot be read.

    Only reached when `real_path` fails to unify two candidates already
    sharing `(provider, package, page_path)` (ADR-0049) -- an ordinary
    symlink alias never pays for this, and it is never persisted.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _cluster_partition(
    members: list[tuple[int, Path | None]], next_id: Callable[[], int]
) -> dict[int, int]:
    """Assign a target_cluster id to each (row index, real_path) pair in one partition.

    Group by `real_path` first (`python`/`python3`/`python3.14`, one mise
    symlink layer, unify here for free). A `real_path` that stays a
    singleton is a proven-distinct *file*, not yet a proven-distinct
    *program* -- hash it, and singletons sharing a hash merge (`pip`/`pip3`/
    `pip3.14`: three `console_scripts` files, one program). A candidate with
    no `Installation` (ADR-0020's unclaimed case) gets its own id untouched;
    it never needed help distinguishing itself.
    """
    by_real_path: dict[Path, list[int]] = {}
    assignment: dict[int, int] = {}
    for index, real_path in members:
        if real_path is None:
            assignment[index] = next_id()
            continue
        by_real_path.setdefault(real_path, []).append(index)

    singletons: list[tuple[Path, int]] = []
    for real_path, indexes in by_real_path.items():
        if len(indexes) > 1:
            cid = next_id()
            for i in indexes:
                assignment[i] = cid
        else:
            singletons.append((real_path, indexes[0]))

    by_digest: dict[str, int] = {}
    for real_path, index in singletons:
        digest = _content_digest(real_path)
        assignment[index] = (
            next_id() if digest is None else by_digest.setdefault(digest, next_id())
        )
    return assignment


def _with_target_clusters(
    rows: list[ToolRow], candidates: list[Candidate]
) -> list[ToolRow]:
    """Attach each row's `target_cluster` (ADR-0049): refine `(provider, package)`
    groups by proven shared identity, never merge across a `page_path` split
    (a differing page already proves two different things regardless of what
    the binaries resolve to).
    """
    partitions: dict[tuple[str, str, Path | None], list[tuple[int, Path | None]]] = {}
    for index, row in enumerate(rows):
        inst = candidates[index].installation
        real_path = inst.real_path if inst is not None else None
        partitions.setdefault((row.provider, row.package, row.page_path), []).append(
            (index, real_path)
        )

    next_id = count().__next__
    assignment: dict[int, int] = {}
    for members in partitions.values():
        assignment.update(_cluster_partition(members, next_id))
    return [replace(row, target_cluster=assignment[i]) for i, row in enumerate(rows)]


def compute_rows(
    tools: list[str] | None = None,
    config: Config | None = None,
    *,
    observer: InventoryObserver | None = None,
) -> list[ToolRow]:
    """One row per binary: every provider-detected installation, or exactly the named tools.

    With no names, walks `$PATH` (`resolution.enumerate_installations`) and
    reports every binary some provider claims. With names, resolves exactly
    those, unfiltered; a name no provider claims still gets a row (`MISSING`,
    unless `man` or the manifest says otherwise) rather than nothing, per
    ADR-0013. Rows come back in the order `observer` saw them.
    """
    cfg = config or Config()
    watcher = observer or InventoryObserver()
    # Entries are frozen dataclasses; the proxy prevents a worker from
    # accidentally changing the single read snapshot while it classifies.
    started_at = monotonic()
    manifest_started_at = monotonic()
    entries = MappingProxyType(manifest.load(cfg))
    manifest_finished_at = monotonic()

    inventory_started_at = monotonic()
    candidates, discovered = _build_inventory(tools, watcher)
    inventory_finished_at = monotonic()
    rows = [_skeleton_row(candidate) for candidate in candidates]
    if discovered:
        watcher.inventory_ready(tuple(rows))
    watcher.rows_started(len(rows))

    coordinator = _RowCoordinator(
        candidates=candidates,
        rows=rows,
        # Upstream completions may legitimately arrive before the last local
        # `man -w`, but cannot change this local-only snapshot.
        local_rows=rows.copy(),
        observer=watcher,
    )
    probe_pending: dict[Future[ProbePage | None], ProbeKey] = {}
    local_published = False
    local_started_at = monotonic()
    local_finished_at = local_started_at
    first_probe_started_at: float | None = None

    with (
        ThreadPoolExecutor(max_workers=LOCAL_CLASSIFY_WORKERS) as local_executor,
        ThreadPoolExecutor(max_workers=UPSTREAM_PROBE_WORKERS) as probe_executor,
    ):
        local_pending: dict[Future[_LocalResult], int] = {
            local_executor.submit(_classify_and_resolve, candidate, cfg, entries): index
            for index, candidate in enumerate(candidates)
        }

        while local_pending or probe_pending:
            done = _next_completed_futures(local_pending, probe_pending, watcher)
            if done is None:
                continue
            # Resolve local futures before completed probes. A duplicate that
            # became ready in the same turn joins the single-flight group.
            launches = _complete_local_futures(done, local_pending, coordinator)

            # The local callback must get one `checking` frame before a fast
            # cache-backed probe can publish its terminal result.
            for key, upstream, inst in launches:
                if first_probe_started_at is None:
                    first_probe_started_at = monotonic()
                probe_pending[
                    probe_executor.submit(probe_upstream, upstream, inst, cfg)
                ] = key

            if not local_pending and not local_published:
                coordinator.publish_local_facts()
                local_published = True
                local_finished_at = monotonic()
            _complete_probe_futures(done, probe_pending, coordinator)

    finished_at = monotonic()
    logger.debug(
        "List inventory timing",
        candidates=len(candidates),
        manifest_seconds=manifest_finished_at - manifest_started_at,
        inventory_seconds=inventory_finished_at - inventory_started_at,
        local_seconds=local_finished_at - local_started_at,
        upstream_seconds=(
            finished_at - first_probe_started_at
            if first_probe_started_at is not None
            else 0.0
        ),
        total_seconds=finished_at - started_at,
    )
    rows = _with_target_clusters(rows, candidates)
    return rows
