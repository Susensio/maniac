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
from ..installer import install_manpage
from ..logging import logger
from ..manifest import Tier
from ..models import Installation, PipelineResult
from ..sources import discovery
from ..sources.docs import discover_repo_manpage
from ..sources.manpages import (
    manpage_documents,
    read_manpage_source,
    select_primary_manpage,
)
from ..sources.providers.base import Provider

__all__ = ["InstallOutcome", "Tier", "run_install"]


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
    """
    cfg = config or Config()
    cache_dir_path = Path(cache_dir) if cache_dir is not None else cfg.cache_dir

    if not generate_only:
        found = discovery.find_installation(tool_name, bin_dir=bin_dir)
        if found is not None:
            provider, inst = found
            outcome = _try_install_root(provider, inst, force=force)
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
    detail = (
        "synthesized from --help + repo docs"
        if pipeline_result.doc_file_count
        else "synthesized from --help only"
    )
    return InstallOutcome(
        tool=tool_name,
        tier=Tier.SYNTHESIS,
        detail=detail,
        source_path=pipeline_result.roff_path,
        installed_path=pipeline_result.installed_path,
        pipeline=pipeline_result,
    )


def _try_install_root(
    provider: Provider, inst: Installation, *, force: bool
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
    source = provider.resolve_source(inst)
    if source is None:
        return None

    page = discover_repo_manpage(
        source, inst.binary, cache_dir=cache_dir_path, config=cfg, version=inst.version
    )
    if page is None:
        return None

    content = read_manpage_source(page)
    if not manpage_documents(content, inst.binary):
        logger.debug(
            "Tier-2 page does not name the binary it claims to document",
            tool=inst.binary,
            path=str(page),
        )
        return None

    installed_path = install_manpage(
        page,
        inst.binary,
        Tier.REPOSITORY,
        source.target,
        force=force,
        version=inst.version,
    )
    detail = f"upstream manpage from repository ({inst.version})   [no synthesis]"
    return InstallOutcome(
        tool=inst.binary,
        tier=Tier.REPOSITORY,
        detail=detail,
        source_path=page,
        installed_path=installed_path,
    )
