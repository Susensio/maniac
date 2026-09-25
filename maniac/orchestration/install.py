"""`install`: pick the highest-tier authoritative source available (ADR-0016).

Tier order, cheapest and most authoritative first: the install root (tier
1), the upstream repository with the version matched (tier 2), then LLM
synthesis (tier 3, `synthesize`). Tiers 1-2 are always tried; `--no-synthesize`
restricts selection to them and must never reach `synthesize` -- tested by
`tests/test_orchestration_install.py` failing a monkeypatched LLM call
reachable through it, not only by asserting the happy path.

One `ResolvedTool` is resolved before the tiers and carries the binary, its
installation, provider, installed version and documentation source through
all three, so tier 3 never re-resolves what tiers 1-2 already found.
"""

import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .. import manifest
from ..config import Config
from ..exceptions import ManiacError
from ..installer import (
    InstallResult,
    PageRequest,
    _discard_materialized_target,
    _remove_orphaned_roff,
    _remove_recorded_manpage,
    install_manpage,
)
from ..logging import logger
from ..manifest import Tier, manpage_owner
from ..models import PipelineResult
from ..sources.candidates import select_install_root, select_repository
from ..sources.docs import discover_repo_manpages
from ..sources.pathcache import resolve_bin_path
from .context import ResolvedTool, resolve_tool

__all__ = ["InstallOutcome", "InstallRefused", "Tier", "run_install"]


class InstallRefused(ManiacError):
    """`install` declined outright, before any tier ran -- not a tier failing.

    `cli/install.py` renders this apart from an ordinary failure: nothing
    was attempted, so "Install failed for X" would misdescribe a refusal.
    """


@dataclass(frozen=True, slots=True)
class InstallOutcome:
    """What `run_install` decided for one tool: the tier that answered, and detail."""

    tool: str
    tier: Tier | None
    detail: str
    source_path: Path | None = None
    installed_path: Path | None = None
    pipeline: PipelineResult | None = None


def run_install(
    tool_name: str,
    *,
    model: str | None = None,
    no_synthesize: bool = False,
    force: bool = False,
    dry_run: bool = False,
    config: Config | None = None,
    bin_dir: str | Path | None = None,
) -> InstallOutcome:
    """Resolve `tool_name` through ADR-0016's tiers and report which one answered.

    Tiers 1-2 (install root, repository) always run first. `no_synthesize`
    (`--no-synthesize`) restricts selection to them -- `synthesize`, the
    only path that can call an LLM, is imported nowhere in that branch, not
    merely left uncalled.

    Before any tier runs (ADR-0061): a binary not on `$PATH` at all -- with
    no explicit `bin_dir` naming where it lives instead -- is refused
    outright. A page installs into a global manpath and persists; a binary
    MANIAC cannot locate at all does not, so nothing here should record one
    for it.
    """
    cfg = config or Config()

    bin_path = resolve_bin_path(tool_name, bin_dir)
    if bin_path is None:
        raise InstallRefused(
            f"'{tool_name}' is not on $PATH -- and a manpage would be "
            "installed globally and permanently. Install it somewhere "
            "$PATH can reach first."
        )

    _refuse_unmanaged_destination(cfg.man_dir / f"{tool_name}.1", cfg, force=force)

    tool = resolve_tool(tool_name, config=cfg, bin_dir=bin_dir)
    if tool.provider is None:
        # ADR-0061: with $PATH inherited rather than login-shell-reconstructed,
        # the first hit can be a project-scoped shadow (a venv, a node_modules/.bin)
        # that no installer claims -- tiers 1-2 are unreachable for it, and any
        # tier-3 page below documents exactly this resolved binary, not a global one.
        logger.info(
            "Binary resolves outside any known installer",
            tool=tool_name,
            resolved_path=str(bin_path),
        )

    outcome = _try_install_root(tool, force=force, dry_run=dry_run)
    if outcome is not None:
        return outcome
    outcome, repository_definitive = _try_repository(tool, force=force, dry_run=dry_run)
    if outcome is not None:
        return outcome
    if no_synthesize:
        return InstallOutcome(
            tool=tool_name,
            tier=None,
            detail=(
                "no install-root or repository page found "
                "(tried tiers 1-2 only; rerun without --no-synthesize to synthesize)"
            ),
        )
    if not repository_definitive:
        source = tool.documentation_source
        detail = source.identity if source is not None else "the upstream repository"
        raise InstallRefused(
            f"Could not confirm whether {detail} has a manpage for '{tool_name}' -- "
            "the tier-2 repository check failed rather than returning a "
            "definitive answer, so synthesizing over it could silently paper "
            "over a network or git failure. Rerun once the check can complete, "
            "or pass --no-synthesize to accept tiers 1-2 only."
        )

    from .pipeline import synthesize  # deferred: tier 3 only, never on --no-synthesize

    pipeline_result = synthesize(
        tool,
        model=model,
        install=True,
        force=force,
        dry_run=dry_run,
    )
    if pipeline_result.command_count and pipeline_result.doc_file_count:
        detail = "synthesized from --help + repo docs"
    elif pipeline_result.doc_file_count:
        detail = "synthesized from repo docs only"
    else:
        detail = "synthesized from --help only"
    if dry_run:
        # A real run's page depends on pandoc compiling the synthesized
        # Markdown (`compile_to_man`); `synthesize` skips that step under
        # `dry_run` entirely, so the preview has to ask the same question
        # pandoc would answer, or it reports success on a machine where the
        # real run would produce no page at all.
        if shutil.which("pandoc") is None:
            return InstallOutcome(
                tool=tool_name,
                tier=None,
                detail=f"{detail}   [dry run, pandoc missing, no page would be produced]",
                source_path=pipeline_result.roff_path,
                installed_path=None,
                pipeline=pipeline_result,
            )
        detail += "   [dry run]"
    return InstallOutcome(
        tool=tool_name,
        tier=Tier.SYNTHESIS,
        detail=detail,
        source_path=pipeline_result.roff_path,
        installed_path=pipeline_result.installed_path,
        pipeline=pipeline_result,
    )


def _refuse_unmanaged_destination(dest_file: Path, cfg: Config, *, force: bool) -> None:
    """Refuse when `dest_file` already holds a foreign or vendor page.

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
    if force:
        return
    if not (dest_file.exists() or dest_file.is_symlink()):
        return
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
    if owned:
        return
    raise InstallRefused(
        f"A foreign or vendor manpage already exists at '{dest_file}'. "
        f"Use --force to create a backup and overwrite."
    )


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
    detail = "upstream manpage from install root"
    if inst.version:
        detail += f" ({inst.version})"
    if dry_run:
        detail += "   [dry run, no synthesis]"
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
    detail += "   [no synthesis]"
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
                detail=f"upstream manpage from repository ({inst.version})"
                "   [dry run, no synthesis]",
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
    detail = f"upstream manpage from repository ({inst.version})   [no synthesis]"
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
