"""Track which pages MANIAC installed (ADR-0017).

The manifest, not a page's own bytes, is what `install`, `uninstall` and
`status` consult to decide ownership. Tier 1 and tier 2 pages are copied
verbatim and carry no marker of their own; a provenance header stays on
tier-3 pages as informational metadata but is never read for this decision.
"""

import json
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from .config import Config

SCHEMA_VERSION = 1


class Tier(Enum):
    """Which of ADR-0016's three source tiers produced a manifest entry."""

    INSTALL_ROOT = "install_root"
    REPOSITORY = "repository"
    SYNTHESIS = "synthesis"


@dataclass(frozen=True, slots=True)
class Entry:
    """One tool's manifest record: where its page lives, and how it got there.

    `backup` is the path of the vendor page `install_manpage --force` backed
    up before overwriting, under `Config.backup_dir` -- or None when install
    found the destination empty and took no backup, distinct from "unknown".
    """

    path: Path
    tier: Tier
    source: str
    backup: Path | None = None


def _manifest_path(config: Config | None) -> Path:
    return (config or Config()).manifest_path


def _entry_to_row(entry: Entry) -> dict[str, Any]:
    return {
        "path": str(entry.path),
        "tier": entry.tier.value,
        "source": entry.source,
        "backup": str(entry.backup) if entry.backup is not None else None,
    }


def _row_to_entry(row: Any) -> Entry | None:
    """Return the entry a row describes, or None for anything malformed.

    A row that fails to parse -- missing key, wrong type, unrecognized tier --
    is skipped rather than voiding the whole manifest.
    """
    if not isinstance(row, dict):
        return None
    try:
        backup = row["backup"]
        return Entry(
            path=Path(row["path"]),
            tier=Tier(row["tier"]),
            source=row["source"],
            backup=Path(backup) if backup is not None else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _seed_from_headers(config: Config | None) -> dict[str, Entry]:
    """Migrate pre-ADR-0017 state: a page carrying a provenance header was synthesized.

    Tiers 1 and 2 copy their page verbatim and carry no header, so they are
    not recoverable here -- accepted in ADR-0017 as the cost of migration,
    since they already reported foreign under the code this replaces.

    `list_installed_manpages` also scans `output_dir`, a staging copy of
    what synthesis produced, not proof anything is on the manpath -- a page
    seeded from there rather than `man_dir` would report MANAGED for a tool
    with nothing installed, so only a `man_dir` hit is kept.
    """
    from .installer import list_installed_manpages  # deferred: breaks the import cycle

    cfg = config or Config()
    entries = {
        item["tool"]: Entry(
            path=item["path"], tier=Tier.SYNTHESIS, source=item["model"]
        )
        for item in list_installed_manpages(config)
        if item["path"].parent == cfg.man_dir
    }
    _migrate_backups(entries, config)
    return entries


def _migrate_backups(entries: dict[str, Entry], config: Config | None) -> None:
    """Relocate stray `.maniac_bak` files out of `man_dir`, attributed by matching filename.

    Repairs the pre-ADR-0017 bug where a backup's name (`dest_file.name`
    plus `.maniac_bak`) never matched what restore reconstructed
    (`f"{tool_name}.1.maniac_bak"`), so a backup of a compressed page was
    created and never found. A backup matching no entry's destination
    filename is left where it is -- nothing on record to attribute it to.
    """
    cfg = config or Config()
    if not cfg.man_dir.exists():
        return
    by_filename = {entry.path.name: tool for tool, entry in entries.items()}
    for backup_file in cfg.man_dir.glob("*.maniac_bak"):
        original_name = backup_file.name.removesuffix(".maniac_bak")
        tool = by_filename.get(original_name)
        if tool is None:
            continue
        cfg.backup_dir.mkdir(parents=True, exist_ok=True)
        destination = cfg.backup_dir / original_name
        backup_file.rename(destination)
        entries[tool] = replace(entries[tool], backup=destination)


def load(config: Config | None = None) -> dict[str, Entry]:
    """Return every recorded entry, keyed by tool.

    Seeds the manifest by scanning for provenance headers when no manifest
    file exists yet, and persists that seed so it runs once. A manifest
    that exists but fails to parse -- corrupt JSON, wrong shape, an
    unrecognized version -- degrades to empty instead of raising; a
    corrupt store costs a rebuild, never a crash. That degradation never
    triggers a reseed, so a manifest emptied by uninstalling everything is
    not mistaken for "never migrated".
    """
    path = _manifest_path(config)
    if not path.exists():
        entries = _seed_from_headers(config)
        _save(path, entries)
        return entries

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(document, dict) or document.get("version") != SCHEMA_VERSION:
        return {}

    raw_entries = document.get("entries")
    if not isinstance(raw_entries, dict):
        return {}

    entries: dict[str, Entry] = {}
    for tool, row in raw_entries.items():
        entry = _row_to_entry(row)
        if entry is not None:
            entries[tool] = entry
    return entries


def _save(path: Path, entries: dict[str, Entry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "version": SCHEMA_VERSION,
        "entries": {tool: _entry_to_row(entry) for tool, entry in entries.items()},
    }
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(document, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary_path.replace(path)


def record(
    tool: str,
    path: Path,
    tier: Tier,
    source: str,
    backup: Path | None = None,
    config: Config | None = None,
) -> None:
    """Record `tool`'s installed page. Called before the copy that places it, per ADR-0017."""
    manifest_path = _manifest_path(config)
    entries = load(config)
    entries[tool] = Entry(path=Path(path), tier=tier, source=source, backup=backup)
    _save(manifest_path, entries)


def lookup(tool: str, config: Config | None = None) -> Entry | None:
    """Return `tool`'s recorded entry, or None if MANIAC never installed it."""
    return load(config).get(tool)


def forget(tool: str, config: Config | None = None) -> None:
    """Remove `tool`'s entry, if any. A no-op if it was never recorded."""
    manifest_path = _manifest_path(config)
    entries = load(config)
    if tool in entries:
        del entries[tool]
        _save(manifest_path, entries)
