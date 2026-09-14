"""`list`: report each binary's manpage reachability, per ADR-0026's action ladder.

| state    | means                                                             |
|----------|--------------------------------------------------------------------|
| ok       | a page resolves through `man` now and is current by local evidence |
| unverified | an external page resolves but its matching package cannot be proven |
| outdated | a page resolves, and positive evidence says it documents another version |
| available| nothing resolves, but a page can be had without an LLM             |
| missing  | nothing resolves and no free page is known                         |

This reverses ADR-0013/ADR-0016's choice, recorded in this module's earlier
docstring as "the manpath is never scanned": `find_installed_manpage_path`
(`sources/manpages.py`, `man -w`) is now called for every row, because the
question this command answers is whether `man <tool>` works, not what
MANIAC itself has done for a binary (ADR-0018). Enumeration is still a
provider walk (`discovery.enumerate_installations`), not a manpath scan --
the unit stays a binary a provider detected, since that is still what
bounds what a bulk install could act on -- but each row's *state* is now a
reachability fact, checked against `man` directly.

`--unverified`/`--outdated`/`--available`/`--missing` filters the State axis; `--managed`
filters manifest ownership independently of page provenance. Filters union
within an axis and intersect across axes; no flags means no filtering. There is no
`--ok`, deliberately (ADR-0018): it would select exactly the rows needing
no action.

Repository identity is resolved for rows that remain locally unresolved per
ADR-0018. Tier-2 manpage lookup is cache-first and runs only for unresolved
rows with an installed version and an installation-derived repository.
"""

from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from time import monotonic
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.live import Live
from rich.progress import Progress
from rich.table import Table

from .. import manifest
from ..config import Config
from ..logging import logger
from ..models import RepoSource
from ..sources import discovery
from ..sources.crawler import get_version
from ..sources.docs import discover_repo_manpage, discovered_manpage_uri
from ..sources.documentation import documentation_source
from ..sources.manpages import (
    _opener_for,
    find_installed_manpage_path,
    select_primary_manpage,
)
from ..sources.packages import ExternalPageFreshness, verify_external_page
from ..sources.pathcache import resolve_cached
from . import app, console, get_config
from .render import _repo_cell

if TYPE_CHECKING:
    from ..models import Installation
    from ..sources.providers.base import Provider


class _ProgressReporter:
    """Progress bar for blocking interactive `list` calls.

    Discovery and per-row reachability report through `on_phase_start` and
    `on_scan`, sharing one task and combined total. Streaming calls stop it
    once the table skeleton is ready and do not send later row callbacks.
    `transient=True` clears it before a final table prints.
    """

    def __init__(self, target_console: Any) -> None:
        self._total = 0
        self._done = 0
        # `Live.start` does `with self.console:` -- a dunder lookup that
        # bypasses `cli._LazyConsole.__getattr__` entirely, so `Progress`
        # needs the real `rich.console.Console` underneath it, not the lazy
        # wrapper. Any forwarded attribute access forces it into existence.
        _ = target_console.is_terminal
        resolved_console = getattr(target_console, "_instance", target_console)
        self._progress = Progress(console=resolved_console, transient=True)
        self._progress.start()
        self._task_id = self._progress.add_task("Scanning...", total=None)

    def on_phase_start(self, total: int) -> None:
        self._total += total
        self._progress.update(self._task_id, total=self._total)

    def on_scan(self) -> None:
        self._done += 1
        self._progress.update(self._task_id, completed=self._done)

    def stop(self) -> None:
        self._progress.stop()


class ActionState(Enum):
    """The action-ladder state a binary's manpage reachability puts it in (ADR-0026)."""

    OK = "ok"
    UNVERIFIED = "unverified"
    OUTDATED = "outdated"
    AVAILABLE = "available"
    MISSING = "missing"


# Traffic-light by what remains to be done: green needs nothing, yellow
# needs a free reinstall or install, red needs an LLM.
_STATE_COLOR: dict[ActionState, str] = {
    ActionState.OK: "green",
    ActionState.UNVERIFIED: "yellow",
    ActionState.OUTDATED: "yellow",
    ActionState.AVAILABLE: "yellow",
    ActionState.MISSING: "red",
}


class PageSource(Enum):
    """Where page content came from, or would come from if installed (ADR-0027).

    One column serves both readings -- State already disambiguates which
    applies, since `ok`/`outdated` describe a page that resolves and
    `available` describes one that would if installed.
    """

    MANIAC = "maniac"
    VENDOR = "vendor"
    UPSTREAM = "upstream"
    SYSTEM = "system"
    NONE = ""


_STATE_COLUMN_WIDTH = max(
    len("checking…"), *(len(state.value) for state in ActionState)
)
_STREAMING_SOURCE_WIDTH = max(len(source.value) for source in PageSource)
_TOOL_COLUMN_MAX_WIDTH = 24
_UPSTREAM_COLUMN_WIDTH = 24


LOCAL_CLASSIFY_WORKERS = 8
UPSTREAM_PROBE_WORKERS = 8


@dataclass(frozen=True, slots=True)
class ToolRow:
    """One binary's reachability. `package` groups siblings for the table view only."""

    tool: str
    package: str
    provider: str
    state: ActionState
    source: PageSource
    upstream: RepoSource | None
    managed: bool = False
    page_path: Path | None = None
    page_uri: str | None = None


@dataclass(frozen=True, slots=True)
class _LocalClassification:
    state: ActionState
    source: PageSource
    managed: bool
    page_path: Path | None
    page_uri: str | None = None


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


def _classify(
    provider: "Provider | None",
    inst: "Installation | None",
    tool: str,
    cfg: Config,
    entries: Mapping[str, manifest.Entry] | None = None,
) -> _LocalClassification:
    """Decide one binary's state and page source by whether `man` resolves it (ADR-0026).

    Reverses `status`'s prior rule of consulting the manifest alone: `man
    -w` is checked first, and the manifest is consulted only to tell a
    MANIAC-owned page apart from one `man` would resolve regardless (a
    distro page, or one a user hand-placed). A manifest entry whose file
    exists but which `man` does not resolve is therefore no longer reported
    MANIAC-owned -- ownership requires reachability now, not only a record.
    """
    installed = find_installed_manpage_path("man", tool)
    # A bulk list reads this immutable snapshot once. Keep the lookup fallback
    # for direct callers and the single-tool classification tests.
    entry = (
        entries.get(tool) if entries is not None else manifest.lookup(tool, config=cfg)
    )
    owned = (
        entry is not None
        and (entry.path.exists() or entry.path.is_symlink())
        and installed is not None
        and _same_page(entry.path, installed)
    )
    provider_target_freshness = _provider_target_freshness(
        provider, inst, entry.target if entry is not None else None
    )
    provider_target_outdated = (
        entry is not None
        and entry.provider_target
        and provider_target_freshness is False
    )
    provider_target_current = (
        entry is not None
        and entry.provider_target
        and provider_target_freshness is True
    )

    if provider_target_outdated:
        return _LocalClassification(
            ActionState.OUTDATED, PageSource.VENDOR, True, entry.path, entry.source_uri
        )

    if installed is not None:
        if owned:
            assert entry is not None
            source = {
                manifest.Tier.INSTALL_ROOT: PageSource.VENDOR,
                manifest.Tier.REPOSITORY: PageSource.UPSTREAM,
                manifest.Tier.SYNTHESIS: PageSource.MANIAC,
            }[entry.tier]
        elif inst is not None and _under_root(installed, inst.root):
            source = PageSource.VENDOR
        else:
            source = PageSource.SYSTEM

        # An unclaimed binary (no provider, no `Installation`) has nothing
        # to compare `entry.version` against by default -- ask the binary
        # itself (ADR-0020). Gated on the cheap checks first: only a row
        # that is owned, carries a recorded version, and has no
        # `Installation` may pay for the subprocess this triggers.
        if owned and entry is not None and entry.version is not None and inst is None:
            current_version = get_version([tool], config=cfg)
        else:
            current_version = inst.version if inst is not None else None

        # `outdated` requires positive evidence -- a recorded version that
        # differs from the installed binary's current one. Absent that
        # evidence (an unowned page, or `local_lib`'s permanent lack of a
        # version) a row reads `ok` (ADR-0018): MANIAC is not entitled to
        # call a page stale it never assessed.
        outdated = (
            owned
            and entry is not None
            and entry.version is not None
            and current_version is not None
            and entry.version != current_version
            and not provider_target_current
        )
        if owned:
            return _LocalClassification(
                ActionState.OUTDATED if outdated else ActionState.OK,
                source,
                True,
                installed,
                entry.source_uri,
            )
        if provider is not None and inst is not None and source is PageSource.SYSTEM:
            freshness = verify_external_page(
                installed, package=inst.package, version=inst.version
            )
            if freshness is ExternalPageFreshness.MATCH:
                return _LocalClassification(ActionState.OK, source, False, installed)
            if freshness is ExternalPageFreshness.MISMATCH:
                return _LocalClassification(
                    ActionState.OUTDATED, source, False, installed
                )
            return _LocalClassification(
                ActionState.UNVERIFIED, source, False, installed
            )
        return _LocalClassification(ActionState.OK, source, False, installed)

    if provider is not None and inst is not None:
        page = select_primary_manpage(provider.local_docs(inst), inst.binary)
        if page is not None:
            return _LocalClassification(
                ActionState.AVAILABLE, PageSource.VENDOR, False, page
            )
    return _LocalClassification(ActionState.MISSING, PageSource.NONE, False, None)


def _provider_target_freshness(
    provider: "Provider | None", inst: "Installation | None", target: Path | None
) -> bool | None:
    """Return a provider-specific target freshness verdict when one exists."""
    checker = getattr(provider, "has_current_latest_manpage_target", None)
    if inst is None or target is None or checker is None:
        return None
    return checker(inst, target)


def _resolve_upstream(
    provider: "Provider | None", inst: "Installation | None", *, config: Config
) -> RepoSource | None:
    """Resolve `inst`'s upstream repository."""
    if provider is None or inst is None:
        return None
    source = provider.resolve_source(inst, config=config)
    return (
        documentation_source(source, config.documentation_repository_overrides)
        if source is not None
        else None
    )


def _probe_upstream(
    source: RepoSource, inst: "Installation", cfg: Config
) -> tuple[Path, str | None] | None:
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


def _is_upstream_eligible(row: ToolRow, inst: "Installation | None") -> bool:
    """Whether one completed local row needs the version-matched remote check."""
    return (
        (
            (row.state is ActionState.MISSING and row.source is PageSource.NONE)
            or (row.source is PageSource.UPSTREAM and row.page_uri is None)
        )
        and row.upstream is not None
        and inst is not None
        and inst.version is not None
    )


def _classify_and_resolve(
    provider: "Provider | None",
    inst: "Installation | None",
    tool: str,
    cfg: Config,
    entries: Mapping[str, manifest.Entry],
) -> tuple[_LocalClassification, RepoSource | None]:
    """Classify locally and resolve repository identity independently."""
    result = _classify(provider, inst, tool, cfg, entries)
    upstream = _resolve_upstream(provider, inst, config=cfg)
    return result, upstream


def _probe_key(source: RepoSource, inst: "Installation") -> tuple[str, str, str]:
    """Identity of one version-pinned availability probe."""
    return (source.clone_url or source.target, inst.version or "", inst.binary)


def _build_inventory(
    tools: list[str] | None,
    on_discovery_start: Callable[[int], None] | None,
    on_discovery_scan: Callable[[], None] | None,
) -> tuple[list[tuple["Provider | None", "Installation | None", str]], bool]:
    """Return requested or discovered candidates and whether discovery ran."""
    if tools:
        candidates = []
        for tool in dict.fromkeys(tools):
            # No `discovery.discover_repo(tool)` fallback when `found` is
            # None: it shares `find_installation`'s own bin-path resolution.
            found = discovery.find_installation(tool)
            provider, inst = found if found else (None, None)
            candidates.append((provider, inst, tool))
        return candidates, False

    discovered = sorted(
        discovery.enumerate_installations(
            on_start=on_discovery_start,
            on_scan=on_discovery_scan,
        ),
        key=lambda item: item[1].binary,
    )
    return [(provider, inst, inst.binary) for provider, inst in discovered], True


def _initial_rows(
    candidates: list[tuple["Provider | None", "Installation | None", str]],
) -> list[ToolRow]:
    """Build the stable row skeleton before local classification begins."""
    return [
        ToolRow(
            tool=tool,
            package=inst.package if inst is not None else tool,
            provider=provider.name if provider is not None else "",
            state=ActionState.MISSING,
            source=PageSource.NONE,
            upstream=None,
        )
        for provider, inst, tool in candidates
    ]


def _row_from_local_classification(
    candidate: tuple["Provider | None", "Installation | None", str],
    classified: _LocalClassification,
    upstream: RepoSource | None,
) -> ToolRow:
    """Apply local classification and repository identity to one skeleton row."""
    provider, inst, tool = candidate
    return ToolRow(
        tool=tool,
        package=inst.package if inst is not None else tool,
        provider=provider.name if provider is not None else "",
        state=classified.state,
        source=classified.source,
        upstream=upstream,
        managed=classified.managed,
        page_path=classified.page_path,
        page_uri=classified.page_uri,
    )


def _with_probe_result(row: ToolRow, page: tuple[Path, str | None] | None) -> ToolRow:
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


def _apply_probe_result(
    rows: list[ToolRow],
    indexes: list[int],
    page: tuple[Path, str | None] | None,
    on_row_scan: Callable[[], None] | None,
    on_upstream_rows: Callable[[list[ToolRow], set[int]], None] | None,
) -> None:
    """Publish a completed deduplicated probe group atomically."""
    for index in indexes:
        rows[index] = _with_probe_result(rows[index], page)
    if on_row_scan is not None:
        for _ in indexes:
            on_row_scan()
    if on_upstream_rows is not None:
        on_upstream_rows(rows.copy(), set(indexes))


def _schedule_local_classifications(
    executor: ThreadPoolExecutor,
    candidates: list[tuple["Provider | None", "Installation | None", str]],
    cfg: Config,
    entries: Mapping[str, manifest.Entry],
) -> dict[Future[tuple[_LocalClassification, RepoSource | None]], int]:
    """Submit every independent local classification to the bounded pool."""
    return {
        executor.submit(
            _classify_and_resolve, provider, inst, tool, cfg, entries
        ): index
        for index, (provider, inst, tool) in enumerate(candidates)
    }


def compute_rows(
    tools: list[str] | None = None,
    config: Config | None = None,
    *,
    on_discovery_start: Callable[[int], None] | None = None,
    on_discovery_scan: Callable[[], None] | None = None,
    on_row_start: Callable[[int], None] | None = None,
    on_row_scan: Callable[[], None] | None = None,
    on_skeleton: Callable[[list[ToolRow]], None] | None = None,
    on_local_row: Callable[[list[ToolRow], int, bool], None] | None = None,
    on_initial_rows: Callable[[list[ToolRow], set[int]], None] | None = None,
    on_upstream_rows: Callable[[list[ToolRow], set[int]], None] | None = None,
    on_idle: Callable[[], None] | None = None,
) -> list[ToolRow]:
    """One row per binary: every provider-detected installation, or exactly the named tools.

    With no names, walks `$PATH` (`discovery.enumerate_installations`) and
    reports every binary some provider claims. With names, resolves
    exactly those, unfiltered; a name no provider claims still gets a row
    (`MISSING`, unless `man` or the manifest says otherwise) rather than
    nothing, per ADR-0013.

    The eight `on_*` callbacks, all `None` by default, are purely additive
    instrumentation for a caller with a console in scope (the CLI command);
    every other caller, including tests, omits them and sees no behaviour
    change. `on_discovery_*` passes straight through to
    `discovery.enumerate_installations` (skipped entirely on the `tools`
    path, which never calls it); `on_row_*` wraps this function's own
    per-row `_classify` loop, whichever path runs it.

    `on_skeleton` receives the complete, alphabetized provider-derived inventory
    before local classification.
    `on_local_row` fills one row and says whether its tier-2 probe is pending.
    `on_initial_rows` receives the fully local snapshot for compatible callers.
    `on_upstream_rows` receives one snapshot per deduplicated probe group.
    `on_idle` runs on the coordinator while worker futures remain pending.
    They keep terminal renderers out of worker-owned mutable state; callers that
    omit them retain the original blocking API.

    Local `man -w` queries run through a bounded executor. As each locally
    unresolved row becomes ready, its version-pinned tier-2 probe enters a
    separate bounded executor; the coordinator alone mutates rows and calls
    render callbacks, preserving their stable order and thread affinity.
    """
    cfg = config or Config()
    # Entries are frozen dataclasses; the proxy prevents a worker from
    # accidentally changing the single read snapshot while it classifies.
    entries = MappingProxyType(manifest.load(cfg))

    candidates, discovered = _build_inventory(
        tools, on_discovery_start, on_discovery_scan
    )
    rows = _initial_rows(candidates)
    if discovered and on_skeleton is not None:
        on_skeleton(rows.copy())
    if on_row_start is not None:
        on_row_start(len(rows))

    # Keep a local-only snapshot for callers that use the older initial
    # callback. Upstream completions may legitimately arrive before the last
    # local `man -w`, but cannot change this snapshot.
    local_rows = rows.copy()
    local_pending: dict[Future[tuple[_LocalClassification, RepoSource | None]], int]
    probe_pending: dict[
        Future[tuple[Path, str | None] | None], tuple[str, str, str]
    ] = {}
    probe_indexes: dict[tuple[str, str, str], list[int]] = {}
    probe_results: dict[tuple[str, str, str], tuple[Path, str | None] | None] = {}
    active_probe_keys: set[tuple[str, str, str]] = set()
    local_eligible: set[int] = set()
    initial_sent = False

    with (
        ThreadPoolExecutor(max_workers=LOCAL_CLASSIFY_WORKERS) as local_executor,
        ThreadPoolExecutor(max_workers=UPSTREAM_PROBE_WORKERS) as probe_executor,
    ):
        local_pending = _schedule_local_classifications(
            local_executor, candidates, cfg, entries
        )

        while local_pending or probe_pending:
            done, _ = wait(
                [*local_pending, *probe_pending],
                timeout=0.25,
                return_when=FIRST_COMPLETED,
            )
            if not done:
                if on_idle is not None:
                    on_idle()
                continue
            # Resolve local futures before completed probes. A duplicate that
            # became ready in the same turn joins the single-flight group.
            launches: list[tuple[tuple[str, str, str], RepoSource, Installation]] = []
            for future in sorted(
                (future for future in done if future in local_pending),
                key=local_pending.__getitem__,
            ):
                index = local_pending.pop(future)
                classified, upstream = future.result()
                _, inst, _ = candidates[index]
                row = _row_from_local_classification(
                    candidates[index], classified, upstream
                )
                rows[index] = row
                local_rows[index] = row
                pending_probe = _is_upstream_eligible(row, inst)
                if pending_probe:
                    assert upstream is not None
                    assert inst is not None
                    key = _probe_key(upstream, inst)
                    probe_indexes.setdefault(key, []).append(index)
                    local_eligible.add(index)
                    if key in probe_results:
                        _apply_probe_result(
                            rows,
                            [index],
                            probe_results[key],
                            on_row_scan,
                            on_upstream_rows,
                        )
                        pending_probe = False
                    elif key not in active_probe_keys:
                        active_probe_keys.add(key)
                        launches.append((key, upstream, inst))
                elif on_row_scan is not None:
                    on_row_scan()
                if on_local_row is not None:
                    on_local_row(rows.copy(), index, pending_probe)

            # The local callback must get one `checking` frame before a fast
            # cache-backed probe can publish its terminal result.
            for key, upstream, inst in launches:
                probe_pending[
                    probe_executor.submit(_probe_upstream, upstream, inst, cfg)
                ] = key

            if not local_pending and not initial_sent:
                if on_initial_rows is not None:
                    on_initial_rows(local_rows.copy(), local_eligible.copy())
                initial_sent = True

            for future in [future for future in done if future in probe_pending]:
                key = probe_pending.pop(future)
                page = future.result()
                probe_results[key] = page
                _apply_probe_result(
                    rows, probe_indexes[key], page, on_row_scan, on_upstream_rows
                )

    return rows


def _bare_names(rows: list[ToolRow]) -> list[str]:
    """Deduplicated binary names in first-seen order, for the pipe-friendly path."""
    seen: dict[str, None] = {}
    for row in rows:
        seen.setdefault(row.tool, None)
    return list(seen)


def _upstream_key(upstream: RepoSource | None) -> tuple[str, bool] | None:
    """Hashable identity of a `RepoSource` by what the Upstream column renders.

    `RepoSource.name` is deliberately excluded: it carries the *binary*
    name, which is never displayed, so including it split groups that
    render identically. `pandoc`, `pandoc-lua` and `pandoc-server` all
    resolve to `jgm/pandoc` and showed as three separate rows purely
    because their `name` fields differed. Two rows may collapse only when
    every cell a reader can see agrees, so the key is exactly the rendered
    cell and nothing behind it.
    """
    if upstream is None:
        return None
    return (upstream.target, upstream.is_local)


def _grouped_for_display(
    rows: list[ToolRow], *, pending: set[str] | None = None
) -> list[tuple[str, ToolRow]]:
    """Collapse binaries sharing one (provider, package, state, source, upstream) into one row.

    A solo group's label is its one tool's name, unchanged. A group of
    several is the package name with a count suffix, e.g. `python (12
    binaries)`, not every sibling's name comma-joined: that joined form is
    unbounded -- a package can expose a dozen-plus binaries under one state
    -- and blows up the Tool column's width, breaking the reader's ability
    to track rows by eye down the table.

    Package identity is a display grouping only -- `pandoc`, `pandoc-lua`
    and `pandoc-server` share one install root but are three separate `man`
    lookups. Source and upstream join provider/package/state in the key so
    two rows differing in either can never collapse into one that hides the
    difference; the returned representative row's other fields (state,
    source, upstream) are shared across the whole group by construction.

    Known defect, left exactly as ADR-0018 found it: a solo group is
    labelled by tool name and a multi-binary group by package name, so one
    package split across two states can still render two rows a reader
    cannot tell apart by label alone (`docs/BACKLOG.md`).
    """
    pending = pending or set()
    groups: dict[
        tuple[str, str, ActionState, PageSource, Any, bool], list[ToolRow]
    ] = {}
    order: list[tuple[str, str, ActionState, PageSource, Any, bool]] = []
    for row in rows:
        key = (
            row.provider,
            row.package,
            row.state,
            row.source,
            _upstream_key(row.upstream),
            row.tool in pending,
        )
        if key not in groups:
            order.append(key)
        groups.setdefault(key, []).append(row)

    rendered: list[tuple[str, ToolRow]] = []
    for key in order:
        group = groups[key]
        representative = group[0]
        label = (
            representative.tool
            if len(group) == 1
            else f"{representative.package} ({len(group)} binaries)"
        )
        rendered.append((label, representative))
    return rendered


def _upstream_cell(upstream: RepoSource | None) -> Any:
    """Render the Upstream column: blank when nothing was resolvable (ADR-0018)."""
    from rich.text import Text

    if upstream is None:
        return Text("")
    return _repo_cell(upstream, blank_when_unresolvable=True)


def _source_cell(row: ToolRow) -> Any:
    """Render page provenance, linked to the selected or reachable page."""
    from rich.style import Style
    from rich.text import Text

    text = Text(row.source.value)
    link = row.page_uri
    if (
        link is None
        and row.page_path is not None
        and row.source is not PageSource.UPSTREAM
    ):
        link = row.page_path.absolute().as_uri()
    if link is not None and row.source is not PageSource.NONE:
        text.stylize(
            Style(link=link),
            0,
            len(text),
        )
    return text


def _filter_rows(
    rows: list[ToolRow],
    *,
    outdated: bool,
    unverified: bool = False,
    available: bool,
    missing: bool,
    managed: bool,
) -> list[ToolRow]:
    """Narrow `rows` by State and independent MANIAC ownership filters.

    No flag set at all means no filtering. `--available --missing` unions
    within the State axis to every row worth acting on; `--managed
    --outdated` intersects across axes to MANIAC's own stale pages. There
    is no `--ok` flag (ADR-0018): it would select exactly the rows needing
    no action.
    """
    state_axis = {
        state
        for state, flag in (
            (ActionState.OUTDATED, outdated),
            (ActionState.UNVERIFIED, unverified),
            (ActionState.AVAILABLE, available),
            (ActionState.MISSING, missing),
        )
        if flag
    }
    if not state_axis and not managed:
        return rows
    return [
        row
        for row in rows
        if (not state_axis or row.state in state_axis) and (not managed or row.managed)
    ]


def _list_table(rows: list[ToolRow]) -> Table:
    """Build the ordinary, completed list table."""
    table = Table(title="Manpage Reachability")
    table.add_column(
        "Tool",
        style="cyan",
        max_width=_TOOL_COLUMN_MAX_WIDTH,
        no_wrap=True,
        overflow="ellipsis",
    )
    table.add_column("State", width=_STATE_COLUMN_WIDTH, no_wrap=True)
    table.add_column("Source", width=_STREAMING_SOURCE_WIDTH, no_wrap=True)
    table.add_column(
        "Upstream",
        width=_UPSTREAM_COLUMN_WIDTH,
        no_wrap=True,
        overflow="ellipsis",
    )

    for label, row in _grouped_for_display(rows):
        state = (
            f"[{_STATE_COLOR[row.state]}]{row.state.value}[/{_STATE_COLOR[row.state]}]"
        )
        table.add_row(label, state, _source_cell(row), _upstream_cell(row.upstream))
    return table


def _streaming_table(
    rows: list[ToolRow], pending: set[int], *, maximum_rows: int | None = None
) -> Any:
    """One fixed row per binary, or a fixed-height leading slice while it updates."""
    from rich.text import Text

    if not rows:
        return Text("No tools to report.", style="yellow")
    visible_rows = rows if maximum_rows is None else rows[:maximum_rows]
    tool_width = min(
        _TOOL_COLUMN_MAX_WIDTH, max(len("Tool"), *(len(row.tool) for row in rows))
    )
    table = Table(title="Manpage Reachability")
    table.add_column(
        "Tool", style="cyan", width=tool_width, no_wrap=True, overflow="ellipsis"
    )
    table.add_column(
        "State",
        width=_STATE_COLUMN_WIDTH,
        no_wrap=True,
        overflow="ellipsis",
    )
    table.add_column(
        "Source",
        width=_STREAMING_SOURCE_WIDTH,
        no_wrap=True,
        overflow="ellipsis",
    )
    table.add_column(
        "Upstream",
        width=_UPSTREAM_COLUMN_WIDTH,
        no_wrap=True,
        overflow="ellipsis",
    )
    for index, row in enumerate(visible_rows):
        state = (
            "[dim]checking…[/dim]"
            if index in pending
            else (
                f"[{_STATE_COLOR[row.state]}]{row.state.value}[/{_STATE_COLOR[row.state]}]"
            )
        )
        table.add_row(row.tool, state, _source_cell(row), _upstream_cell(row.upstream))
    hidden_rows = len(rows) - len(visible_rows)
    if hidden_rows:
        table.add_row(
            Text(f"… {hidden_rows} more tools", style="dim"),
            Text(""),
            Text(""),
            Text("full table after completion", style="dim"),
        )
    return table


def _render_list(
    target_console: Any, rows: list[ToolRow], *, names: bool = False
) -> None:
    """Render as a Rich table on a terminal, or bare tool names otherwise.

    Bare names is what makes `maniac list | xargs maniac install` work: no
    table, no colour, no header, one binary per line -- plain `print`, not
    the Rich console, so nothing in a tool's name can be misread as markup,
    and never collapsed by package here, since `install` acts on binaries.
    `--names` forces the same output on a real terminal. `rows` is expected
    to already be filtered (ADR-0018: what is displayed and what is piped
    never disagree), so this renders exactly what it is given.
    """
    if names or not target_console.is_terminal:
        for tool in _bare_names(rows):
            print(tool)
        return

    if not rows:
        target_console.print("[yellow]No tools to report.[/yellow]")
        return

    target_console.print(_list_table(rows))


class _StreamingList:
    """One fixed Live table whose facts fill in after provider discovery."""

    def __init__(self, target_console: Any, reporter: Any) -> None:
        self._console = target_console
        self._reporter = reporter
        self._live: Live | None = None
        self._pending: set[int] = set()
        self._rows: list[ToolRow] = []
        self._last_refresh = 0.0
        self._alternate_screen = False
        self._row_limit: int | None = None
        self._dirty = False

    def _live_row_limit(self, rows: list[ToolRow]) -> int | None:
        """Leading data-row capacity, reserving one row for overflow when needed."""
        rich_console = getattr(self._console, "_instance", self._console)
        # A one-line table row has five fixed lines: title, top border, header,
        # header border, and bottom border. Avoid rendering the whole inventory
        # just to learn that a tall table does not fit.
        full_capacity = max(1, rich_console.size.height - 5)
        if len(rows) <= full_capacity:
            return None
        return max(1, full_capacity - 1)

    def skeleton(self, rows: list[ToolRow]) -> None:
        self._reporter.stop()
        self._row_limit = self._live_row_limit(rows)
        self._alternate_screen = self._row_limit is not None
        self._live = Live(
            _streaming_table(rows, set(range(len(rows))), maximum_rows=self._row_limit),
            console=getattr(self._console, "_instance", self._console),
            auto_refresh=False,
            screen=self._alternate_screen,
            vertical_overflow="crop" if self._alternate_screen else "ellipsis",
        )
        self._live.start()
        self._rows = rows
        self._pending = set(range(len(rows)))
        self._dirty = False
        # Rich renders the initial frame in `start`; defer the first callback
        # refresh so it cannot immediately repaint the same geometry.
        self._last_refresh = monotonic()

    def local(self, rows: list[ToolRow], index: int, upstream_pending: bool) -> None:
        self._rows = rows
        if not upstream_pending:
            self._pending.discard(index)
        self._dirty = True
        self._publish(final=not self._pending)

    def upstream(self, rows: list[ToolRow], indexes: set[int]) -> None:
        self._rows = rows
        self._pending.difference_update(indexes)
        self._dirty = True
        self._publish(final=not self._pending)

    def idle(self) -> None:
        """Flush a throttled frame while the coordinator waits on remote work."""
        self._publish()

    def _publish(self, *, final: bool = False) -> None:
        if self._live is None or not self._dirty:
            return
        now = monotonic()
        if not final and now - self._last_refresh < 0.25:
            return
        self._live.update(
            _streaming_table(
                self._rows,
                self._pending,
                maximum_rows=self._row_limit,
            ),
            refresh=False,
        )
        self._dirty = False
        # Completion gets Rich's one refresh in `Live.stop`; refreshing here
        # would repaint the final frame twice in rapid succession.
        if not final:
            self._live.refresh()
            self._last_refresh = now

    def stop(self, *, completed: bool = True) -> bool:
        """Stop Live and say whether a completed table belongs on the normal screen."""
        if self._live is not None:
            if completed:
                self._publish(final=True)
            if not completed and not self._alternate_screen:
                # Rich otherwise persists its final (and incomplete) frame on failure.
                self._live.transient = True
            self._live.stop()
            self._live = None
        return completed and self._alternate_screen


@app.command(name="list")
def list_tools(
    ctx: typer.Context,
    tools: Annotated[
        list[str] | None,
        typer.Argument(
            help="Tools to report on. With none, every binary a provider detects."
        ),
    ] = None,
    names: Annotated[
        bool,
        typer.Option(
            "--names",
            help="Force bare tool names, one per line, even on a terminal.",
        ),
    ] = False,
    outdated: Annotated[
        bool,
        typer.Option(
            "--outdated",
            help="Only rows positively proven to document another version.",
        ),
    ] = False,
    unverified: Annotated[
        bool,
        typer.Option(
            "--unverified",
            help="Only rows whose external page cannot be proven current.",
        ),
    ] = False,
    available: Annotated[
        bool,
        typer.Option(
            "--available", help="Only rows with a free page not yet installed."
        ),
    ] = False,
    missing: Annotated[
        bool,
        typer.Option("--missing", help="Only rows with no known free page."),
    ] = False,
    managed: Annotated[
        bool,
        typer.Option(
            "--managed", help="Only rows whose reachable page MANIAC installed."
        ),
    ] = False,
) -> None:
    """Report each binary's manpage reachability states."""
    interactive = not names and console.is_terminal
    # A filter selects final-state membership, so a provisional row could lie
    # by appearing or disappearing. Keep those calls blocking; the unfiltered
    # terminal inventory is the path that streams in place.
    streaming = (
        interactive
        and not tools
        and not any((outdated, unverified, available, missing, managed))
    )
    reporter = _ProgressReporter(console) if interactive else None
    renderer = _StreamingList(console, reporter) if streaming and reporter else None

    def discovery_start(total: int) -> None:
        if reporter is not None:
            reporter.on_phase_start(total)

    completed = False
    normal_final = False
    try:
        rows = compute_rows(
            tools,
            config=get_config(ctx),
            on_discovery_start=discovery_start if interactive else None,
            on_discovery_scan=reporter.on_scan if reporter else None,
            # The skeleton stops the streaming progress display. Do not keep
            # updating its hidden task while the table owns feedback.
            on_row_start=reporter.on_phase_start
            if reporter and not streaming
            else None,
            on_row_scan=reporter.on_scan if reporter and not streaming else None,
            on_skeleton=renderer.skeleton if renderer else None,
            on_local_row=renderer.local if renderer else None,
            on_upstream_rows=renderer.upstream if renderer else None,
            on_idle=renderer.idle if renderer else None,
        )
        completed = True
    finally:
        if renderer is not None:
            normal_final = renderer.stop(completed=completed)
        if reporter is not None:
            reporter.stop()
    rows = _filter_rows(
        rows,
        outdated=outdated,
        unverified=unverified,
        available=available,
        missing=missing,
        managed=managed,
    )
    if normal_final:
        # Alt-screen Live deliberately disappears on success; leave one complete,
        # fixed-row table in the normal scrollback instead.
        console.print(_streaming_table(rows, set()))
    elif not streaming:
        _render_list(console, rows, names=names)
