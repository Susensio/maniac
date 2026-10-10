"""`install`: pick the highest-tier authoritative source available (ADR-0016).

Tier order, cheapest and most authoritative first: the install root (tier
1), the upstream repository with the version matched (tier 2), then LLM
synthesis (tier 3, `synthesize`). Tiers 1-2 are always tried; `--no-generate`
restricts selection to them and must never reach `synthesize` -- tested by
`tests/test_orchestration_install.py` failing a monkeypatched LLM call
reachable through it, not only by asserting the happy path.

One `ResolvedTool` is resolved before the tiers and carries the binary, its
installation, provider, installed version and documentation source through
all three, so tier 3 never re-resolves what tiers 1-2 already found.
"""

import shutil
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from .. import manifest
from ..config import Config
from ..exceptions import (
    ManiacError,
    NotGloballySelected,
    ShimRunsNothing,
    UnsupportedPandoc,
)
from ..generation.compiler import require_supported_pandoc
from ..installer import (
    InstallResult,
    PageRequest,
    _discard_materialized_target,
    _remove_orphaned_roff,
    _remove_recorded_manpage,
    install_manpage,
)
from ..listing.models import ActionState
from ..logging import logger
from ..manifest import Tier, manpage_owner
from ..models import PipelineResult
from ..sources.candidates import select_install_root, select_repository
from ..sources.crawler import get_version
from ..sources.docs import discover_repo_manpages
from ..sources.manpages import find_installed_manpage_path
from ..sources.pathcache import resolve_bin_path
from ..sources.resolution import binary_here, is_system_binary
from .context import ResolvedTool, resolve_tool

__all__ = ["InstallOutcome", "InstallRefused", "Tier", "run_install"]


class InstallRefused(ManiacError):
    """`install` declined outright, before any tier ran -- not a tier failing.

    `cli/install.py` renders this apart from an ordinary failure: nothing
    was attempted, so "Install failed for X" would misdescribe a refusal.
    """


@dataclass(frozen=True, slots=True)
class Resolution:
    """The binary a page documents, as `install` prints it (CONTRACT.md rule 2).

    `version` is the installer's own record, or for a binary no installer
    claims, the first line of its own `--version`; None when neither says.
    `here` is the copy the invoking shell would run, set only when it is a
    different file from `binary`.
    """

    binary: Path
    installer: str | None
    version: str | None
    here: Path | None = None


@dataclass(frozen=True, slots=True)
class InstallOutcome:
    """What `run_install` decided for one tool: the tier that answered, and detail."""

    tool: str
    tier: Tier | None
    detail: str
    source_path: Path | None = None
    installed_path: Path | None = None
    pipeline: PipelineResult | None = None
    resolution: Resolution | None = None
    overrides: tuple[str, ...] = ()
    """Each refusal `--force` overrode, said the way the user reads it."""
    unchanged: bool = False
    """The page was already current and left alone: installed, not redone."""


def run_install(
    tool_name: str,
    *,
    model: str | None = None,
    no_generate: bool = False,
    force: bool = False,
    dry_run: bool = False,
    config: Config | None = None,
    bin_dir: str | Path | None = None,
    copy: Path | None = None,
) -> InstallOutcome:
    """Resolve `tool_name` through ADR-0016's tiers and report which one answered.

    `copy` reinstalls a page for exactly the binary it recorded (`update`,
    for a page `--force` pinned): the earlier choice stands, so no refusal
    that `--force` overrode is asked again.

    Tiers 1-2 (install root, repository) always run first. `no_generate`
    (`--no-generate`) restricts selection to them -- `synthesize`, the
    only path that can call an LLM, is imported nowhere in that branch, not
    merely left uncalled.

    The page documents the copy the login shell runs from `$HOME`
    (CONTRACT.md rule 2). Where maniac would refuse -- the tool is not
    installed globally, it is a system package's binary, or a page maniac
    did not install sits at the destination -- `force` installs anyway, and
    the outcome lists each refusal it overrode. A tool with no global copy
    is then documented as the invoking shell's copy, recorded on the page.
    """
    cfg = config or Config()
    overrides: list[str] = []

    # Before resolving anything: a refusal here should cost nothing.
    dest_file = cfg.man_dir / f"{tool_name}.1"
    _refuse_unmanaged_destination(dest_file, cfg, force=force)
    if force and _holds_a_foreign_page(dest_file, cfg):
        overrides.append(
            f"replaces a page maniac did not install at {dest_file}, kept as a backup"
        )

    if copy is not None:
        if not copy.is_file():
            raise InstallRefused(
                f"'{tool_name}' documents {copy}, which is gone; `maniac install "
                f"{tool_name}` documents the copy your login shell runs now."
            )
        tool = resolve_tool(tool_name, config=cfg, bin_dir=copy.parent, here=True)
        outcome = _select_page(
            tool, model=model, no_generate=no_generate, force=force, dry_run=dry_run
        )
        return replace(outcome, resolution=_resolution(tool, bin_dir=copy.parent))

    try:
        tool = _resolve_global(tool_name, cfg, bin_dir)
    except InstallRefused as refusal:
        if not force:
            raise
        this_copy = binary_here(tool_name)
        if this_copy is None:
            raise
        overrides.append(f"not installed globally; documenting {this_copy}")
        tool = resolve_tool(tool_name, config=cfg, bin_dir=this_copy.parent, here=True)
        logger.debug("Forced past a refusal", tool=tool_name, refusal=str(refusal))

    if tool.provider is None and tool.bin_path is not None:
        system_page = _system_binary_page(tool_name, tool.bin_path, force=force)
        if system_page is not None:
            overrides.append(system_page)
            tool = replace(tool, pinned=True)

    if not force and bin_dir is None:
        current = already_current(tool_name, cfg)
        if current is not None:
            return replace(current, resolution=_resolution(tool, bin_dir=None))

    outcome = _select_page(
        tool,
        model=model,
        no_generate=no_generate,
        force=force,
        dry_run=dry_run,
    )
    return replace(
        outcome,
        resolution=_resolution(tool, bin_dir=None if tool.pinned else bin_dir),
        overrides=tuple(overrides),
    )


def already_current(tool_name: str, cfg: Config) -> InstallOutcome | None:
    """maniac's page for `tool_name`, when `list` reads it `ok` for this copy.

    Installing it again would redo the work -- for a generated page, another
    model call -- to land the same page. Only a page `list` reads `ok`, with
    its link sound and documenting the copy the login shell runs, is left
    alone: an `outdated` or `unknown` page, a replaced link, or one pinned to
    another copy is reinstalled, and `--force` reinstalls any page.
    """
    from ..listing.pages import managed_pages

    rows, _ = managed_pages(cfg, [tool_name])
    if not rows:
        return None
    [row] = rows
    entry = manifest.lookup(tool_name, config=cfg)
    if (
        entry is None
        or row.state is not ActionState.OK
        or row.drift
        or row.copy is not None
    ):
        return None
    return InstallOutcome(
        tool=tool_name,
        tier=entry.tier,
        detail=f"already up to date ({row.documented}); --force reinstalls it",
        installed_path=entry.path,
        unchanged=True,
    )


def _resolve_global(
    tool_name: str, cfg: Config, bin_dir: str | Path | None
) -> ResolvedTool:
    """The copy the login `$PATH` reaches, or `InstallRefused` saying why not."""
    bin_path = resolve_bin_path(tool_name, bin_dir)
    if bin_path is None:
        raise InstallRefused(
            f"'{tool_name}' is not on your login shell's $PATH -- and a "
            "manpage would be installed globally and permanently. Install it "
            "globally first, or make your login profile put it on $PATH."
            + _force_hint(tool_name)
        )
    try:
        return resolve_tool(tool_name, config=cfg, bin_dir=bin_dir)
    except ShimRunsNothing as e:
        raise InstallRefused(
            f"'{tool_name}' is a Mise shim ({e.path}) that runs nothing from "
            "$HOME: no globally selected tool provides it, and nothing else on "
            "your login shell's $PATH does. Select it globally "
            "(`mise use -g ...`) to document it." + _force_hint(tool_name)
        ) from e
    except NotGloballySelected as e:
        raise InstallRefused(
            f"'{tool_name}' resolves to {e.path}, which is not one of Mise's "
            "globally selected tools (`mise ls --current` from $HOME) -- "
            "selected only by a project config, or by none. Select it "
            "globally (`mise use -g ...`) or remove the stale link."
            + _force_hint(tool_name)
        ) from e


def _force_hint(tool_name: str) -> str:
    """How to document the invoking shell's copy instead, when it has one."""
    this_copy = binary_here(tool_name)
    if this_copy is None:
        return ""
    return f" This shell runs {this_copy}; `--force` documents that copy."


def _resolution(tool: ResolvedTool, *, bin_dir: str | Path | None) -> Resolution | None:
    """What `tool` documents, and what the invoking shell runs if it differs."""
    if tool.bin_path is None:
        return None
    if tool.installation is not None:
        installer, version = tool.installation.provider, tool.installation.version
    else:
        installer = None
        reported = get_version([str(tool.bin_path)], config=tool.config)
        version = reported.splitlines()[0].strip() if reported else None
    here = binary_here(tool.tool_name) if bin_dir is None and not tool.pinned else None
    if here is not None and _same_file(here, tool.bin_path):
        here = None
    return Resolution(tool.bin_path, installer, version, here)


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


def _system_binary_page(tool_name: str, bin_path: Path, *, force: bool) -> str | None:
    """Refuse a system package's binary, or under `force` say what is overridden.

    CONTRACT.md rule 1 with ADR-0059: its package ships and upgrades its
    page, so maniac leaves it alone unless told to. None when `bin_path` is
    not a system binary.
    """
    if not is_system_binary(bin_path):
        return None
    page = find_installed_manpage_path("man", tool_name)
    if force:
        return (
            f"a system package's binary; this page hides {page}"
            if page is not None
            else "a system package's binary"
        )
    state = (
        f"its package maintains its page, and `man {tool_name}` already shows {page}"
        if page is not None
        else "and its package ships no page for it"
    )
    sep = ":" if page is not None else ","
    raise InstallRefused(
        f"'{tool_name}' is {bin_path}, a system package's binary{sep} {state}. "
        "maniac leaves system tools to their package; `--force` installs one anyway."
    )


def _select_page(
    tool: ResolvedTool,
    *,
    model: str | None,
    no_generate: bool,
    force: bool,
    dry_run: bool,
) -> InstallOutcome:
    """The first tier with a page for `tool`, installed (ADR-0016)."""
    tool_name = tool.tool_name
    outcome = _try_install_root(tool, force=force, dry_run=dry_run)
    if outcome is not None:
        return outcome
    outcome, repository_definitive = _try_repository(tool, force=force, dry_run=dry_run)
    if outcome is not None:
        return outcome
    source = tool.documentation_source
    upstream = source.identity if source is not None else "the upstream repository"
    if no_generate:
        # An incomplete check is not an absence (CONTRACT.md rule 4): say
        # which, so "no page" is never reported on missing evidence.
        return InstallOutcome(
            tool=tool_name,
            tier=None,
            detail=(
                "no shipped or upstream page, and --no-generate forbids generating one"
                if repository_definitive
                else f"the check for an upstream page in {upstream} did not "
                "complete (network or git?), so whether one exists is unknown; "
                "rerun once it can"
            ),
        )
    if not repository_definitive:
        # The same words `--no-generate` uses, plus why nothing was generated:
        # a page generated over a failed check could hide the real one.
        raise InstallRefused(
            f"the check for an upstream page in {upstream} did not complete "
            "(network or git?), so whether one exists is unknown and maniac "
            "will not generate one in its place; rerun once the check can "
            f"complete, or see `maniac why {tool_name}`"
        )

    from .pipeline import synthesize  # deferred: tier 3 only, never on --no-generate

    pipeline_result = synthesize(
        tool,
        model=model,
        install=True,
        force=force,
        dry_run=dry_run,
    )
    detail = _generated_detail(pipeline_result)
    if dry_run:
        # A real run's page depends on pandoc compiling the synthesized
        # Markdown (`compile_to_man`); `synthesize` skips that step under
        # `dry_run` entirely, so the preview has to ask the same question
        # pandoc would answer, or it reports success on a machine where the
        # real run would produce no page at all.
        try:
            pandoc = require_supported_pandoc()
        except UnsupportedPandoc as e:
            return InstallOutcome(
                tool=tool_name,
                tier=None,
                detail=f"{detail}, but {e.found} is too old to compile it",
                source_path=pipeline_result.roff_path,
                installed_path=None,
                pipeline=pipeline_result,
            )
        if pandoc is None:
            return InstallOutcome(
                tool=tool_name,
                tier=None,
                detail=f"{detail}, but pandoc is missing to compile it",
                source_path=pipeline_result.roff_path,
                installed_path=None,
                pipeline=pipeline_result,
            )
    elif pipeline_result.installed_path is None:
        detail += ", but it could not be compiled into a page"
    return InstallOutcome(
        tool=tool_name,
        tier=Tier.SYNTHESIS,
        detail=detail,
        source_path=pipeline_result.roff_path,
        installed_path=pipeline_result.installed_path,
        pipeline=pipeline_result,
    )


def _generated_detail(result: PipelineResult) -> str:
    """What a generated page was written from, as `install` reports it."""
    sources = []
    if result.command_count:
        commands = result.command_count
        sources.append(f"--help ({commands} command{'s' if commands != 1 else ''})")
    if result.doc_file_count:
        docs = f"{result.doc_file_count} doc{'s' if result.doc_file_count != 1 else ''}"
        if result.repo_source is not None:
            docs += f" in {result.repo_source.identity}"
        sources.append(docs)
    return "generated page, from " + " and ".join(sources)


def _refuse_unmanaged_destination(dest_file: Path, cfg: Config, *, force: bool) -> None:
    """Refuse when `dest_file` already holds a foreign or vendor page.

    Under `force`, return instead: `installer._take_backup` backs the page
    up and replaces it (CONTRACT.md rule 1's one exception).

    A fast, cheap version of `installer._take_backup`'s own check -- same
    ownership rule, run early enough that a refused install never crawls
    `--help`, writes a context snapshot, calls an LLM, or (tier 2) installs
    part of a release only to undo it once a later page in the group turns
    out foreign.  Each call site passes the destination(s) it is actually
    about to write: the run-level call below still only knows tier 3's
    default guess (`<tool>.1`, before anything is resolved), while
    `_try_install_root` and `_try_repository` each call this again against
    their own candidate's resolved destination(s) once that candidate is
    built -- `_take_backup` stays the real enforcement point this previews.
    """
    if force or not _holds_a_foreign_page(dest_file, cfg):
        return
    raise InstallRefused(
        f"A manpage maniac did not install already exists at '{dest_file}'. "
        f"Use --force to create a backup and overwrite."
    )


def _holds_a_foreign_page(dest_file: Path, cfg: Config) -> bool:
    """Whether a page maniac did not install sits at `dest_file`."""
    if not (dest_file.exists() or dest_file.is_symlink()):
        return False
    entries = manifest.load(cfg)
    owned = any(
        entry.path == dest_file and manifest.is_expected_link(entry)
        for entry in entries.values()
    )
    if not owned and dest_file.is_symlink():
        # `manifest.load` reads the raw file, missing what `manifest.transaction`
        # would adopt on the way in (`_adopt_orphans`): a crash between
        # linking and recording leaves MANIAC's own page here with no
        # manifest row yet. `is_owned_symlink` is the exact rule
        # `_entry_from_link` uses to adopt such an orphan, so it is owned
        # here too rather than refused as foreign -- and the two places can
        # no longer drift apart, being the one predicate.
        owned = manifest.is_owned_symlink(dest_file, cfg)
    return not owned


@dataclass(frozen=True, slots=True)
class _PageInstall:
    """One page's queued `install_manpage` call, for `_install_page_group`."""

    source_file: Path
    tool: str
    request: PageRequest
    target_dir: Path
    durable_source: bool = False


def _install_page_group(
    items: Iterable[_PageInstall],
    txn: manifest.Transaction,
    cfg: Config,
    *,
    force: bool,
) -> list[InstallResult]:
    """Install every queued page as one unit, in `txn`, undoing all on any failure.

    Shared by tier 1 (`_try_install_root`) and tier 2 (`_try_repository`): a
    multi-page release installs the same way regardless of which tier found
    it -- one transaction around the whole group, so its entries land in a
    single write or none of them do (ADR-0046, ADR-0042).
    """
    # Snapshot before this loop's own puts, for ADR-0050's undo below --
    # `txn.entries` is mutated in place by every `install_manpage` call
    # through `manifest.joined`, so checking target usage against it at
    # undo time would see the very entry now being undone.
    baseline_entries = dict(txn.entries)
    installed: list[InstallResult] = []
    try:
        for item in items:
            result = install_manpage(
                item.source_file,
                item.tool,
                item.request,
                target_dir=item.target_dir,
                force=force,
                durable_source=item.durable_source,
                transaction=txn,
            )
            installed.append(result)
    except Exception:
        # A later page failed: undo every earlier page this loop already
        # installed, in reverse order, before the transaction's own exit
        # discards the manifest side (ADR-0046).
        for result in reversed(installed):
            _undo_installed_page(result, cfg, baseline_entries)
        raise
    # The loop reached here clean: every page's own reinstall stands, so
    # any throwaway undo copy taken for it (ADR-0051's reused-own-target
    # gap) was never needed and would otherwise sit under `backup_dir`
    # forever with nothing left to reference or restore it.
    for result in installed:
        if result.attempt_backup and result.backup_path is not None:
            result.backup_path.unlink(missing_ok=True)
    return installed


def _try_install_root(
    tool: ResolvedTool, *, force: bool, dry_run: bool
) -> InstallOutcome | None:
    """Tier 1: a page already inside the install root, the installed version by construction."""
    provider, inst = tool.provider, tool.installation
    if provider is None or inst is None:
        return None
    candidate = select_install_root(provider, inst)
    if candidate is None:
        return None
    cfg = tool.config
    # Every page of the install root installs as one unit, same as tier 2
    # (ADR-0042, ADR-0046) -- a companion's own destination refuses the
    # whole group before any of them is materialized, not only the primary's.
    for page in candidate.pages:
        dest_name = (
            candidate.final_target.name if page == candidate.primary else page.path.name
        )
        _refuse_unmanaged_destination(
            _manpage_directory(page.path, cfg) / dest_name, cfg, force=force
        )
    detail = "shipped page"
    if inst.version:
        detail += f" ({inst.version})"
    if dry_run:
        return InstallOutcome(
            tool=inst.binary,
            tier=Tier.INSTALL_ROOT,
            detail=detail,
            source_path=candidate.discovered_page,
            installed_path=None,
        )
    # Every page of one install root records the primary's manifest key as
    # its group, the same fact tier 2 records for one release archive
    # (ADR-0042).  The primary is keyed on `inst.binary` -- not
    # `manpage_owner(candidate.primary.path)` -- because its filename can
    # match the subcommand pattern (`select_primary_manpage`) rather than
    # `inst.binary` verbatim.
    group = inst.binary
    with manifest.transaction(cfg) as txn:
        installed = _install_page_group(
            (
                _PageInstall(
                    source_file=candidate.final_target
                    if page == candidate.primary
                    else page.path,
                    tool=inst.binary
                    if page == candidate.primary
                    else manpage_owner(page.path),
                    request=PageRequest(
                        Tier.INSTALL_ROOT,
                        str(inst.root),
                        version=inst.version,
                        provider_target=candidate.provider_owned
                        if page == candidate.primary
                        else False,
                        group=group,
                        binary=tool.documented_binary,
                    ),
                    target_dir=_manpage_directory(page.path, cfg),
                    durable_source=candidate.provider_owned
                    if page == candidate.primary
                    else False,
                )
                for page in candidate.pages
            ),
            txn,
            cfg,
            force=force,
        )
    installed_path = next(
        result.path
        for result, page in zip(installed, candidate.pages, strict=True)
        if page == candidate.primary
    )

    return InstallOutcome(
        tool=inst.binary,
        tier=Tier.INSTALL_ROOT,
        detail=detail,
        source_path=candidate.discovered_page,
        installed_path=installed_path,
    )


def _try_repository(
    tool: ResolvedTool, *, force: bool, dry_run: bool
) -> tuple[InstallOutcome | None, bool]:
    """Tier 2: a hand-authored page fetched from the resolved repository at the matching tag.

    Refuses rather than guesses in two cases ADR-0016 calls out: no
    installed version to match against (`inst.version` is None -- a raw
    `local_lib` checkout, for one), and no upstream tag naming that version
    (`discover_repo_manpage`'s `version=` argument -> `resolve_repo_dir`).
    A page that clears both is still checked against the binary it claims
    to document (`manpage_documents`) before being trusted verbatim.

    The `bool` alongside the outcome is whether a `None` outcome is a
    definitive tier-2 miss (nothing to install, safe to fall through) or a
    probe that failed to complete (`run_install` refuses synthesis rather
    than treat that the same as a definitive absence).
    """
    inst = tool.installation
    if inst is None or inst.version is None:
        return None, True
    source = tool.documentation_source
    if source is None:
        return None, True
    cfg = tool.config

    candidate, definitive = select_repository(
        source,
        inst.binary,
        cache_dir=tool.cache_dir,
        config=cfg,
        version=inst.version,
        discover=discover_repo_manpages,
    )
    if candidate is None:
        return None, definitive
    # Every page of the group installs somewhere (ADR-0042, ADR-0046) --
    # a companion's own destination refuses the whole group before any of
    # them is materialized, not only the primary's.
    for page in candidate.pages:
        _refuse_unmanaged_destination(
            _manpage_directory(page.path, cfg) / page.path.name, cfg, force=force
        )

    if dry_run:
        return (
            InstallOutcome(
                tool=inst.binary,
                tier=Tier.REPOSITORY,
                detail=f"upstream page ({inst.version})",
                source_path=candidate.primary.path,
                installed_path=None,
            ),
            True,
        )

    # Every page of one release archive records the primary's owner as its
    # group: they arrived together and uninstall together, which `source_uri`
    # cannot say -- it names the asset, not the unit, and cannot mark a primary.
    group = manpage_owner(candidate.primary.path)
    # One transaction around the whole loop, not one per page: the release's
    # entries land in a single write or none of them do, so a failure partway
    # cannot record a group naming a primary that was never written
    # (ADR-0046, ADR-0042).  The pages are already materialized by here, so
    # no generation happens under the lock (ADR-0043).
    with manifest.transaction(cfg) as txn:
        installed = _install_page_group(
            (
                _PageInstall(
                    source_file=page.path,
                    tool=manpage_owner(page.path),
                    request=PageRequest(
                        Tier.REPOSITORY,
                        source.identity,
                        version=inst.version,
                        source_uri=page.uri,
                        group=group,
                        binary=tool.documented_binary,
                    ),
                    target_dir=_manpage_directory(page.path, cfg),
                )
                for page in candidate.pages
            ),
            txn,
            cfg,
            force=force,
        )
        kept = {manpage_owner(page.path) for page in candidate.pages}
        _prune_dropped_group_members(group, kept, txn, cfg)
    installed_path = next(
        result.path
        for result, page in zip(installed, candidate.pages, strict=True)
        if page == candidate.primary
    )
    detail = f"upstream page ({inst.version})"
    return (
        InstallOutcome(
            tool=inst.binary,
            tier=Tier.REPOSITORY,
            detail=detail,
            source_path=candidate.primary.path,
            installed_path=installed_path,
        ),
        True,
    )


def _prune_dropped_group_members(
    group: str, kept: set[str], txn: manifest.Transaction, cfg: Config
) -> None:
    """Forget a group member this reinstall's release no longer ships.

    `group` names the primary just (re)installed by `_try_repository`;
    `kept` is the manifest key of every page that install actually put
    down this time. An entry still carrying `group` but missing from
    `kept` was recorded by an earlier release of the same upstream that
    shipped a page this one has dropped -- left alone, the manifest would
    go on naming a page uninstall can never find (docs/BACKLOG.md, "Prune
    a release group when its upstream drops a page"). Cleaned up through
    the same `_remove_recorded_manpage`/`_remove_orphaned_roff` pair
    `_uninstall_group` uses on a removed member, so a retargeted link is
    left in place exactly as uninstall would leave it (`_is_modified`),
    and lands with the rest of this reinstall in the one write `txn`
    commits at exit -- nothing here writes on its own.
    """
    dropped = [
        member
        for member, entry in txn.entries.items()
        if entry.group == group and member not in kept
    ]
    removed_paths: list[Path] = []
    changed_paths: list[Path] = []
    restored_paths: list[Path] = []
    for member in dropped:
        entry, _foreign, kept_modified = _remove_recorded_manpage(
            member,
            txn,
            removed_paths=removed_paths,
            changed_paths=changed_paths,
            restored_paths=restored_paths,
        )
        if kept_modified:
            continue
        _remove_orphaned_roff(member, cfg, entry, removed_paths)


def _undo_installed_page(
    result: InstallResult, cfg: Config, baseline_entries: dict[str, manifest.Entry]
) -> None:
    """Undo one page whose own `install_manpage` call already succeeded (ADR-0051).

    Unlike `installer._restore_or_discard_backup`, this never checks whether
    `result.path` still exists: by construction, only a page whose call
    returned successfully ever reaches here, so `link_manpath_entry` already
    completed its atomic replace and `result.path` is definitely the live
    symlink now being torn down, not a pre-call state to infer.  Checking
    `_path_exists` here would see the dangling symlink `_discard_materialized_target`
    is about to create and wrongly discard the one surviving backup instead
    of restoring it.
    """
    _discard_materialized_target(result.materialized, cfg, baseline_entries)
    result.path.unlink(missing_ok=True)
    if result.backup_path is not None:
        shutil.move(result.backup_path, result.path)


def _manpage_directory(page: Path, cfg: Config) -> Path:
    """Use the configured man1 directory for section 1 and its manpath sibling otherwise."""
    path = (
        page.with_suffix("") if page.suffix in {".gz", ".bz2", ".xz", ".zst"} else page
    )
    section = path.suffix.removeprefix(".")
    return cfg.man_dir if section == "1" else cfg.man_dir.parent / f"man{section}"
