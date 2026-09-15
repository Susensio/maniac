"""`install`: pick the highest-tier authoritative source available (ADR-0016).

Tier order, cheapest and most authoritative first: the install root (tier
1), the upstream repository with the version matched (tier 2), then LLM
synthesis (tier 3, `synthesize`). `--generate` restricts selection to tier
3; `--no-generate` restricts it to tiers 1-2 and must never reach
`synthesize` -- tested by `tests/test_orchestration_install.py` failing a
monkeypatched LLM call reachable through it, not only by asserting the
happy path.

One `ResolvedTool` is resolved before the tiers and carries the binary, its
installation, provider, installed version and documentation source through
all three, so tier 3 never re-resolves what tiers 1-2 already found.
"""

from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..exceptions import ManiacError
from ..installer import install_manpage
from ..manifest import Tier
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
    cache_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    prompt_file: str | Path | None = None,
    model: str | None = None,
    generate_only: bool = False,
    no_generate: bool = False,
    force: bool = False,
    dry_run: bool = False,
    config: Config | None = None,
    bin_dir: str | Path | None = None,
) -> InstallOutcome:
    """Resolve `tool_name` through ADR-0016's tiers and report which one answered.

    `generate_only` (`--generate`) restricts selection to tier 3, skipping
    tiers 1-2 outright. `no_generate` (`--no-generate`) restricts it to
    tiers 1-2 -- `synthesize`, the only path that can call an LLM, is
    imported nowhere in that branch, not merely left uncalled.

    Before any tier runs (ADR-0020): a binary the login `$PATH` cannot reach
    -- with no explicit `bin_dir` naming where it lives instead -- is refused
    outright. A page installs into a global manpath and persists; a binary
    reachable only from the current environment does not, so nothing here
    should record one for it.
    """
    cfg = config or Config()

    # Ahead of the `generate_only` branch, not inside it: the refusal asks
    # whether MANIAC should serve this binary at all, which is prior to
    # which tier would answer. Inside the branch, `--generate` bypassed it
    # and synthesized a global page for a binary only this shell can see --
    # the exact outcome ADR-0020 exists to prevent.
    if resolve_bin_path(tool_name, bin_dir) is None:
        raise InstallRefused(
            f"'{tool_name}' is reachable only from the current "
            "environment, not the login shell's $PATH -- and a manpage "
            "would be installed globally and permanently. Install it "
            "where the login shell can reach it first (ADR-0020)."
        )

    tool = resolve_tool(tool_name, config=cfg, cache_dir=cache_dir, bin_dir=bin_dir)

    if not generate_only:
        outcome = _try_install_root(tool, force=force) or _try_repository(
            tool, force=force
        )
        if outcome is not None:
            return outcome
        if no_generate:
            return InstallOutcome(
                tool=tool_name,
                tier=None,
                detail=(
                    "no install-root or repository page found "
                    "(tried tiers 1-2 only; rerun with --generate to synthesize)"
                ),
            )

    from .pipeline import synthesize  # deferred: tier 3 only, never on --no-generate

    pipeline_result = synthesize(
        tool,
        output_dir=output_dir,
        prompt_file=prompt_file,
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
    return InstallOutcome(
        tool=tool_name,
        tier=Tier.SYNTHESIS,
        detail=detail,
        source_path=pipeline_result.roff_path,
        installed_path=pipeline_result.installed_path,
        pipeline=pipeline_result,
    )


def _try_install_root(tool: ResolvedTool, *, force: bool) -> InstallOutcome | None:
    """Tier 1: a page already inside the install root, the installed version by construction."""
    provider, inst = tool.provider, tool.installation
    if provider is None or inst is None:
        return None
    candidate = select_install_root(provider, inst)
    if candidate is None:
        return None
    installed_path = install_manpage(
        candidate.final_target,
        inst.binary,
        Tier.INSTALL_ROOT,
        str(inst.root),
        force=force,
        version=inst.version,
        durable_source=candidate.provider_owned,
        provider_target=candidate.provider_owned,
        config=tool.config,
    )
    detail = "upstream manpage from install root"
    if inst.version:
        detail += f" ({inst.version})"
    detail += "   [no synthesis]"
    return InstallOutcome(
        tool=inst.binary,
        tier=Tier.INSTALL_ROOT,
        detail=detail,
        source_path=candidate.discovered_page,
        installed_path=installed_path,
    )


def _try_repository(tool: ResolvedTool, *, force: bool) -> InstallOutcome | None:
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

    installed_path: Path | None = None
    # Every page of one release archive records the primary's owner as its
    # group: they arrived together and uninstall together, which `source_uri`
    # cannot say -- it names the asset, not the unit, and cannot mark a primary.
    group = _manpage_owner(candidate.primary.path)
    for page in candidate.pages:
        installed = install_manpage(
            page.path,
            _manpage_owner(page.path),
            Tier.REPOSITORY,
            source.identity,
            target_dir=_manpage_directory(page.path, cfg),
            force=force,
            version=inst.version,
            source_uri=page.uri,
            group=group,
            config=cfg,
        )
        if page == candidate.primary:
            installed_path = installed
    assert installed_path is not None
    detail = f"upstream manpage from repository ({inst.version})   [no synthesis]"
    return InstallOutcome(
        tool=inst.binary,
        tier=Tier.REPOSITORY,
        detail=detail,
        source_path=candidate.primary.path,
        installed_path=installed_path,
    )


def _manpage_owner(page: Path) -> str:
    """Return the manpage name without its section or compression suffix."""
    path = page
    if path.suffix in {".gz", ".bz2", ".xz", ".zst"}:
        path = path.with_suffix("")
    return path.with_suffix("").name


def _manpage_directory(page: Path, cfg: Config) -> Path:
    """Use the configured man1 directory for section 1 and its manpath sibling otherwise."""
    path = (
        page.with_suffix("") if page.suffix in {".gz", ".bz2", ".xz", ".zst"} else page
    )
    section = path.suffix.removeprefix(".")
    return cfg.man_dir if section == "1" else cfg.man_dir.parent / f"man{section}"
