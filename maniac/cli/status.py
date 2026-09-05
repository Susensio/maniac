"""`status`: report tools MANIAC could act on, or exactly the tools named.

Per ADR-0013: with no arguments, the tools with a source resolvable under
ADR-0008's installation-tied rule, plus those MANIAC already manages. With
arguments, exactly those tools, unfiltered -- the escape hatch that removes
the need for `--bin-dir`, `--system` or `--all`.

Columns are observations, never a verdict word or a state name, per
ADR-0012 and ADR-0014: this module never recomputes classification, it only
reads `classification.collect_facts()`'s cache.
"""

from dataclasses import dataclass
from typing import Annotated, Any

import typer
from rich.table import Table

from ..candidates import CandidateSelection, select_candidate
from ..classification import ManpageFacts, collect_facts
from . import app, console, default_cfg
from .render import _repo_cell


@dataclass(frozen=True, slots=True)
class StatusRow:
    """One manpage's observations: word count, flag-entry count, ownership, source."""

    facts: ManpageFacts


def _actionable(facts: ManpageFacts) -> bool:
    """Whether a page belongs in the no-argument listing.

    A resolvable installation-tied source means MANIAC could improve it; a
    page it already manages stays visible even if a source cannot be
    re-resolved.
    """
    return bool(facts.sources) or facts.is_maniac_authored


def compute_status(
    tools: list[str] | None = None, candidates_only: bool = False
) -> list[StatusRow]:
    """Compute status rows from classification facts. Never recomputes classification.

    With no tool names, only actionable pages (see `_actionable`). With tool
    names, every matching page found in the classification scan, unfiltered.
    A named tool absent from the scan (no installed manpage at all) reports
    nothing -- classification facts only exist for pages the manpath scan
    can see.

    `candidates_only` applies after that selection, per ADR-0014: it keeps
    only pages `candidates.select_candidate` marks SELECTED against
    `Config.min_words_per_flag`. A page with `NO_EVIDENCE` (unrecognised
    dialect, or no countable flag entries) is never selected, however poor
    it is -- under-claiming is the deliberate bias, not a bug here.
    """
    all_facts = collect_facts()
    if tools:
        wanted = set(tools)
        matching = [facts for facts in all_facts if facts.tool in wanted]
    else:
        matching = [facts for facts in all_facts if _actionable(facts)]

    if candidates_only:
        threshold = default_cfg.min_words_per_flag
        matching = [
            facts
            for facts in matching
            if select_candidate(facts, threshold) is CandidateSelection.SELECTED
        ]

    return [StatusRow(facts=facts) for facts in matching]


def _bare_names(rows: list[StatusRow]) -> list[str]:
    """Deduplicated tool names in first-seen order, for the pipe-friendly path."""
    seen: dict[str, None] = {}
    for row in rows:
        seen.setdefault(row.facts.tool, None)
    return list(seen)


def _render_status(
    target_console: Any, rows: list[StatusRow], *, names: bool = False
) -> None:
    """Render as a Rich table on a terminal, or bare tool names otherwise.

    Bare names is what makes `maniac status --candidates | xargs maniac
    generate` and `maniac generate $(maniac status --candidates)` work: no
    table, no colour, no header, one name per line -- plain `print`, not the
    Rich console, so nothing in a tool's name can be misread as markup.
    """
    if names or not target_console.is_terminal:
        for tool in _bare_names(rows):
            print(tool)
        return

    if not rows:
        target_console.print("[yellow]No tools to report.[/yellow]")
        return

    table = Table(title="Manpage Status")
    table.add_column("Tool", style="cyan")
    table.add_column("Section", justify="center")
    table.add_column("Words", justify="right")
    table.add_column("Flag entries", justify="right")
    table.add_column("Owner")
    table.add_column("Source", overflow="fold")

    for row in rows:
        facts = row.facts
        source_cell = _repo_cell(facts.sources[0]) if facts.sources else "-"
        table.add_row(
            facts.tool,
            facts.section,
            str(facts.word_count),
            str(facts.tp_count),
            "MANIAC" if facts.is_maniac_authored else "vendor",
            source_cell,
        )

    target_console.print(table)


@app.command()
def status(
    tools: Annotated[
        list[str] | None,
        typer.Argument(
            help="Tools to report on. With none, every tool MANIAC could act on."
        ),
    ] = None,
    candidates: Annotated[
        bool,
        typer.Option(
            "--candidates",
            help="Only pages MANIAC's internal heuristic flags as improvable.",
        ),
    ] = False,
    names: Annotated[
        bool,
        typer.Option(
            "--names",
            help="Force bare tool names, one per line, even on a terminal.",
        ),
    ] = False,
) -> None:
    """Report tools MANIAC could act on, or exactly the tools named."""
    rows = compute_status(tools, candidates_only=candidates)
    _render_status(console, rows, names=names)
