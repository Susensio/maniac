"""Classify installed manpages into stored facts, never a derived verdict.

See docs/adr/0012-classification-facts-not-state.md: the useful answer for a
page depends on two independent facts -- its own condition and whether
MANIAC has sources to do better -- and no single verdict word carries both,
so this module stops at the facts. What should be done about a page is a
presentation-layer question, computed elsewhere, over these facts, at
display time.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

from .installer import read_provenance_header
from .models import RepoSource
from .sources.discovery import discover_candidate_source
from .sources.manpages import (
    Dialect,
    Generator,
    HelpDerivedManpage,
    count_tp_entries,
    count_words,
    detect_dialect_from_content,
    detect_generator_from_content,
    extract_sections,
    find_installed_manpages,
    has_examples_section,
    read_manpage_source,
)

CACHE_FILENAME = "classification.json"

# Stand-in `path` for a tool with no installed page. Keeps `ManpageFacts.path`
# non-optional, so the cache round-trip needs no None case; absence rows are
# never cached, since the cache key is (path, mtime, size) and an absent page
# has none of the three.
ABSENT_PATH = Path("<absent>")

# Bump on any change to ManpageFacts's fields (add, remove, rename, retype).
# A mismatch discards the whole cache instead of crashing on stale rows.
CACHE_SCHEMA_VERSION = 3


@dataclass(frozen=True, slots=True)
class ManpageFacts:
    """Stored classification facts for one installed manpage. No verdict."""

    tool: str
    section: str
    path: Path
    exists: bool
    is_maniac_authored: bool
    generator: Generator | None
    dialect: Dialect
    word_count: int
    tp_count: int
    sections: list[str]
    has_examples_section: bool
    sources: list[RepoSource]


def _cache_path() -> Path:
    """Return MANIAC's XDG cache location for classification facts."""
    cache_home = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return cache_home / "maniac" / CACHE_FILENAME


def _cache_key(path: Path) -> tuple[float, int] | None:
    """Return the (mtime, size) identity a cached row must match to be reused."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime, stat.st_size


def _load_cache(cache_path: Path) -> dict[str, Any]:
    """Return the cached rows, or an empty cache for any version mismatch or corruption."""
    try:
        document = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if (
        not isinstance(document, dict)
        or document.get("version") != CACHE_SCHEMA_VERSION
    ):
        return {}
    rows = document.get("rows")
    return rows if isinstance(rows, dict) else {}


def _save_cache(cache_path: Path, rows: dict[str, Any]) -> None:
    """Write the cache atomically, keeping only rows seen in this scan."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": CACHE_SCHEMA_VERSION, "rows": rows}
    temporary_path = cache_path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(document, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary_path.replace(cache_path)


def _source_to_row(source: RepoSource) -> dict[str, Any]:
    return {
        "name": source.name,
        "target": source.target,
        "is_local": source.is_local,
        "local_path": str(source.local_path) if source.local_path else None,
    }


def _row_to_source(row: dict[str, Any]) -> RepoSource:
    return RepoSource(
        name=row["name"],
        target=row["target"],
        is_local=row["is_local"],
        local_path=Path(row["local_path"]) if row["local_path"] else None,
    )


def _facts_to_row(facts: ManpageFacts) -> dict[str, Any]:
    return {
        "tool": facts.tool,
        "section": facts.section,
        "path": str(facts.path),
        "exists": facts.exists,
        "is_maniac_authored": facts.is_maniac_authored,
        "generator": facts.generator.value if facts.generator else None,
        "dialect": facts.dialect.value,
        "word_count": facts.word_count,
        "tp_count": facts.tp_count,
        "sections": facts.sections,
        "has_examples_section": facts.has_examples_section,
        "sources": [_source_to_row(source) for source in facts.sources],
    }


def _row_to_facts(row: dict[str, Any]) -> ManpageFacts:
    return ManpageFacts(
        tool=row["tool"],
        section=row["section"],
        path=Path(row["path"]),
        exists=row["exists"],
        is_maniac_authored=row["is_maniac_authored"],
        generator=Generator(row["generator"]) if row["generator"] else None,
        dialect=Dialect(row["dialect"]),
        word_count=row["word_count"],
        tp_count=row["tp_count"],
        sections=row["sections"],
        has_examples_section=row["has_examples_section"],
        sources=[_row_to_source(source) for source in row["sources"]],
    )


def _facts_from_cache(cache_entry: Any, key: tuple[float, int]) -> ManpageFacts | None:
    """Return the cached facts for a matching key, or None to force recomputation.

    A row that fails to parse -- missing key, wrong type, bad enum value --
    is treated as a miss rather than raising; a cache is an optimisation, and
    corruption in one row should cost recomputing that row, not the command.
    """
    if not isinstance(cache_entry, dict):
        return None
    try:
        if tuple(cache_entry["key"]) != key:
            return None
        return _row_to_facts(cache_entry["facts"])
    except (KeyError, TypeError, ValueError):
        return None


def _classify_page(page: HelpDerivedManpage) -> ManpageFacts:
    """Compute facts for one page: read it once, resolve its source once."""
    content = read_manpage_source(page.path)
    source = discover_candidate_source(page.name)
    dialect = detect_dialect_from_content(content)
    sections = extract_sections(content, dialect)
    return ManpageFacts(
        tool=page.name,
        section=page.section,
        path=page.path,
        exists=True,
        is_maniac_authored=read_provenance_header(page.path) is not None,
        generator=detect_generator_from_content(content),
        dialect=dialect,
        word_count=count_words(content),
        tp_count=count_tp_entries(content, dialect),
        sections=sections,
        has_examples_section=has_examples_section(sections),
        sources=[source] if source else [],
    )


def absent_facts(tool: str) -> ManpageFacts:
    """Facts for a named tool with no installed page: empty everywhere but the source.

    Source resolution goes through the same `discover_candidate_source` as
    `_classify_page`, which is what makes the row worth printing -- it names
    the source MANIAC would generate the missing page from. Nothing here is
    measured, so every observation is the zero value rather than a claim.
    """
    source = discover_candidate_source(tool)
    return ManpageFacts(
        tool=tool,
        section="",
        path=ABSENT_PATH,
        exists=False,
        is_maniac_authored=False,
        generator=None,
        dialect=Dialect.UNKNOWN,
        word_count=0,
        tp_count=0,
        sections=[],
        has_examples_section=False,
        sources=[source] if source else [],
    )


def collect_facts(
    manpath_bin: str = "manpath", cache_path: Path | None = None
) -> list[ManpageFacts]:
    """Classify every installed manpage, reusing cached rows keyed by (path, mtime, size).

    Stats every page on each run; only a changed key triggers recomputation.
    Rows for pages no longer found in the scan are dropped from the cache.
    """
    resolved_cache_path = cache_path or _cache_path()
    cache = _load_cache(resolved_cache_path)
    fresh_cache: dict[str, Any] = {}
    facts: list[ManpageFacts] = []

    for page in find_installed_manpages(manpath_bin):
        key = _cache_key(page.path)
        if key is None:
            continue

        page_facts = _facts_from_cache(cache.get(str(page.path)), key)
        if page_facts is None:
            page_facts = _classify_page(page)

        fresh_cache[str(page.path)] = {
            "key": list(key),
            "facts": _facts_to_row(page_facts),
        }
        facts.append(page_facts)

    _save_cache(resolved_cache_path, fresh_cache)
    return sorted(facts, key=lambda item: (item.tool, item.section))


app = typer.Typer(
    help="Manpage classification facts -- internal, not a user-facing command."
)


@app.command()
def dump(manpath_bin: str = "manpath") -> None:
    """Print one row per installed manpage with every classification fact."""
    for facts in collect_facts(manpath_bin):
        origin = facts.generator.value if facts.generator else "none"
        sources = "; ".join(source.target for source in facts.sources) or "none"
        sections = ", ".join(facts.sections) or "none"
        print(
            f"{facts.tool}({facts.section})\t{facts.path}\t"
            f"authored={facts.is_maniac_authored}\tgenerator={origin}\t"
            f"dialect={facts.dialect.value}\t"
            f"word_count={facts.word_count}\ttp_count={facts.tp_count}\t"
            f"has_examples_section={facts.has_examples_section}\t"
            f"sections=[{sections}]\tsources={sources}"
        )


if __name__ == "__main__":
    app()
