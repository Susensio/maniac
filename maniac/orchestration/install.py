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
from dataclasses import dataclass
from pathlib import Path

from .. import manifest
from ..config import Config
from ..exceptions import ManiacError
from ..installer import (
    InstallResult,
    _discard_materialized_target,
    _restore_or_discard_backup,
    draft_entry,
    install_manpage,
)
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

    Before any tier runs (ADR-0020): a binary the login `$PATH` cannot reach
    -- with no explicit `bin_dir` naming where it lives instead -- is refused
    outright. A page installs into a global manpath and persists; a binary
    reachable only from the current environment does not, so nothing here
    should record one for it.
    """
    cfg = config or Config()

    if resolve_bin_path(tool_name, bin_dir) is None:
        raise InstallRefused(
            f"'{tool_name}' is reachable only from the current "
            "environment, not the login shell's $PATH -- and a manpage "
            "would be installed globally and permanently. Install it "
            "where the login shell can reach it first (ADR-0020)."
        )

    _refuse_unmanaged_destination(tool_name, cfg, force=force)

    tool = resolve_tool(tool_name, config=cfg, bin_dir=bin_dir)

    outcome = _try_install_root(tool, force=force, dry_run=dry_run) or _try_repository(
        tool, force=force, dry_run=dry_run
    )
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


def _refuse_unmanaged_destination(tool_name: str, cfg: Config, *, force: bool) -> None:
    """Refuse before any tier runs when the default manpath destination is foreign.

    A fast, cheap version of `installer._take_backup`'s own check, run before
    a refused install can crawl `--help`, write a context snapshot or call an
    LLM for nothing.  It only knows the common destination (`<tool>.1`); a
    tier that resolves a different one -- a different section, a compressed
    extension, a page not named after the tool -- still passes here and
    still meets `_take_backup`, which stays the enforcement point.
    """
    if force:
        return
    dest_file = cfg.man_dir / f"{tool_name}.1"
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
    installed_path = install_manpage(
        candidate.final_target,
        inst.binary,
        draft_entry(
            Tier.INSTALL_ROOT,
            str(inst.root),
            version=inst.version,
            provider_target=candidate.provider_owned,
        ),
        force=force,
        durable_source=candidate.provider_owned,
        config=tool.config,
    ).path
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
) -> InstallOutcome | None:
    """Tier 2: a hand-authored page fetched from the resolved repository at the matching tag.

    Refuses rather than guesses in two cases ADR-0016 calls out: no
    installed version to match against (`inst.version` is None -- a raw
    `local_lib` checkout, for one), and no upstream tag naming that version
    (`discover_repo_manpage`'s `version=` argument -> `resolve_repo_dir`).
    A page that clears both is still checked against the binary it claims
    to document (`manpage_documents`) before being trusted verbatim.
    """
    inst = tool.installation
    if inst is None or inst.version is None:
        return None
    source = tool.documentation_source
    if source is None:
        return None
    cfg = tool.config

    candidate = select_repository(
        source,
        inst.binary,
        cache_dir=tool.cache_dir,
        config=cfg,
        version=inst.version,
        discover=discover_repo_manpages,
    )
    if candidate is None:
        return None

    if dry_run:
        return InstallOutcome(
            tool=inst.binary,
            tier=Tier.REPOSITORY,
            detail=f"upstream manpage from repository ({inst.version})"
            "   [dry run, no synthesis]",
            source_path=candidate.primary.path,
            installed_path=None,
        )

    installed_path: Path | None = None
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
        # Snapshot before this loop's own puts, for ADR-0050's undo below --
        # `txn.entries` is mutated in place by every `install_manpage` call
        # through `manifest.joined`, so checking target usage against it at
        # undo time would see the very entry now being undone.
        baseline_entries = dict(txn.entries)
        installed: list[InstallResult] = []
        try:
            for page in candidate.pages:
                result = install_manpage(
                    page.path,
                    manpage_owner(page.path),
                    draft_entry(
                        Tier.REPOSITORY,
                        source.identity,
                        version=inst.version,
                        source_uri=page.uri,
                        group=group,
                    ),
                    target_dir=_manpage_directory(page.path, cfg),
                    force=force,
                    transaction=txn,
                )
                installed.append(result)
                if page == candidate.primary:
                    installed_path = result.path
        except Exception:
            # A later page failed: undo every earlier page this loop already
            # installed, in reverse order, before the transaction's own exit
            # discards the manifest side (ADR-0046).
            for result in reversed(installed):
                _discard_materialized_target(result.materialized, cfg, baseline_entries)
                if result.backup_path is not None:
                    _restore_or_discard_backup(result.backup_path, result.path)
            raise
    assert installed_path is not None
    detail = f"upstream manpage from repository ({inst.version})   [no synthesis]"
    return InstallOutcome(
        tool=inst.binary,
        tier=Tier.REPOSITORY,
        detail=detail,
        source_path=candidate.primary.path,
        installed_path=installed_path,
    )


def _manpage_directory(page: Path, cfg: Config) -> Path:
    """Use the configured man1 directory for section 1 and its manpath sibling otherwise."""
    path = (
        page.with_suffix("") if page.suffix in {".gz", ".bz2", ".xz", ".zst"} else page
    )
    section = path.suffix.removeprefix(".")
    return cfg.man_dir if section == "1" else cfg.man_dir.parent / f"man{section}"
