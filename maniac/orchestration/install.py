"""`install`: pick the highest-tier authoritative source available (ADR-0016).

Tier order, cheapest and most authoritative first: the install root (tier
1), the upstream repository with the version matched (tier 2), then LLM
synthesis (tier 3, `run_pipeline`). `--generate` restricts selection to
tier 3; `--no-generate` restricts it to tiers 1-2 and must never reach
`run_pipeline` -- tested by `tests/test_orchestration_install.py` failing a
monkeypatched LLM call reachable through it, not only by asserting the
happy path.
"""

from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..exceptions import ManiacError
from ..installer import install_manpage
from ..logging import logger
from ..manifest import Tier
from ..models import Installation, PipelineResult
from ..sources import discovery
from ..sources.docs import discover_repo_manpages
from ..sources.documentation import documentation_source
from ..sources.manpages import (
    manpage_documents,
    read_manpage_source,
    select_primary_manpage,
)
from ..sources.providers.base import Provider

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
    intermediate_dir: str | Path | None = None,
    prompt_file: str | Path | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
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
    tiers 1-2 -- `run_pipeline`, the only path that can call an LLM, is
    imported nowhere in that branch, not merely left uncalled.

    Before any tier runs (ADR-0020): a binary the login `$PATH` cannot reach
    -- with no explicit `bin_dir` naming where it lives instead -- is refused
    outright. A page installs into a global manpath and persists; a binary
    reachable only from the current environment does not, so nothing here
    should record one for it.
    """
    cfg = config or Config()
    cache_dir_path = Path(cache_dir) if cache_dir is not None else cfg.cache_dir

    # Ahead of the `generate_only` branch, not inside it: the refusal asks
    # whether MANIAC should serve this binary at all, which is prior to
    # which tier would answer. Inside the branch, `--generate` bypassed it
    # and synthesized a global page for a binary only this shell can see --
    # the exact outcome ADR-0020 exists to prevent.
    if discovery.resolve_bin_path(tool_name, bin_dir) is None:
        raise InstallRefused(
            f"'{tool_name}' is reachable only from the current "
            "environment, not the login shell's $PATH -- and a manpage "
            "would be installed globally and permanently. Install it "
            "where the login shell can reach it first (ADR-0020)."
        )

    if not generate_only:
        found = discovery.find_installation(tool_name, bin_dir=bin_dir)
        if found is not None:
            provider, inst = found
            outcome = _try_install_root(provider, inst, cfg=cfg, force=force)
            if outcome is not None:
                return outcome
            outcome = _try_repository(
                provider,
                inst,
                cache_dir_path=cache_dir_path,
                cfg=cfg,
                force=force,
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

    from .pipeline import run_pipeline  # deferred: tier 3 only, never on --no-generate

    pipeline_result = run_pipeline(
        tool_name,
        cache_dir=cache_dir,
        output_dir=output_dir,
        intermediate_dir=intermediate_dir,
        prompt_file=prompt_file,
        model=model,
        reasoning_effort=reasoning_effort,
        install=True,
        force=force,
        dry_run=dry_run,
        config=cfg,
        bin_dir=bin_dir,
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


def _try_install_root(
    provider: Provider, inst: Installation, *, cfg: Config, force: bool
) -> InstallOutcome | None:
    """Tier 1: a page already inside the install root, the installed version by construction."""
    page = select_primary_manpage(provider.local_docs(inst), inst.binary)
    if page is None:
        return None

    installed_path = install_manpage(
        page,
        inst.binary,
        Tier.INSTALL_ROOT,
        str(inst.root),
        force=force,
        version=inst.version,
        config=cfg,
    )
    detail = "upstream manpage from install root"
    if inst.version:
        detail += f" ({inst.version})"
    detail += "   [no synthesis]"
    return InstallOutcome(
        tool=inst.binary,
        tier=Tier.INSTALL_ROOT,
        detail=detail,
        source_path=page,
        installed_path=installed_path,
    )


def _try_repository(
    provider: Provider,
    inst: Installation,
    *,
    cache_dir_path: Path,
    cfg: Config,
    force: bool,
) -> InstallOutcome | None:
    """Tier 2: a hand-authored page fetched from the resolved repository at the matching tag.

    Refuses rather than guesses in two cases ADR-0016 calls out: no
    installed version to match against (`inst.version` is None -- a raw
    `local_lib` checkout, for one), and no upstream tag naming that version
    (`discover_repo_manpage`'s `version=` argument -> `resolve_repo_dir`).
    A page that clears both is still checked against the binary it claims
    to document (`manpage_documents`) before being trusted verbatim.
    """
    if inst.version is None:
        return None
    source = provider.resolve_source(inst, config=cfg)
    if source is None:
        return None
    source = documentation_source(source, cfg.documentation_repository_overrides)

    pages = discover_repo_manpages(
        source, inst.binary, cache_dir=cache_dir_path, config=cfg, version=inst.version
    )
    if not pages:
        return None

    page = pages[0]
    content = read_manpage_source(page)
    if not manpage_documents(content, inst.binary):
        logger.debug(
            "Tier-2 page does not name the binary it claims to document",
            tool=inst.binary,
            path=str(page),
        )
        return None

    installed_path: Path | None = None
    for candidate in pages:
        installed = install_manpage(
            candidate,
            _manpage_owner(candidate),
            Tier.REPOSITORY,
            source.target,
            target_dir=_manpage_directory(candidate, cfg),
            force=force,
            version=inst.version,
            config=cfg,
        )
        if candidate == page:
            installed_path = installed
    assert installed_path is not None
    detail = f"upstream manpage from repository ({inst.version})   [no synthesis]"
    return InstallOutcome(
        tool=inst.binary,
        tier=Tier.REPOSITORY,
        detail=detail,
        source_path=page,
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
