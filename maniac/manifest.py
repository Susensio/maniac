"""Track which pages MANIAC installed (ADR-0017, ADR-0028).

The manifest, not a page's own bytes, is what `install`, `uninstall` and
`status` consult to decide ownership.  Its recorded target establishes the
expected manpath link; a provenance header on tier-3 pages is informational.
"""

import hashlib
import json
import shutil
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from .config import Config
from .logging import logger

# Unbumped for the `version` field (ADR-0018): a mismatch here empties the
# whole manifest on load, and an absent `version` already reads None on its
# own -- no migration is needed to make a missing key readable.
SCHEMA_VERSION = 1
_CHUNK_SIZE = 65_536


class Tier(Enum):
    """Which of ADR-0016's three source tiers produced a manifest entry."""

    INSTALL_ROOT = "install_root"
    REPOSITORY = "repository"
    SYNTHESIS = "synthesis"


@dataclass(frozen=True, slots=True)
class Entry:
    """One tool's manifest record: where its page lives, how it got there, and its bytes' hash.

    `backup` is the path of the vendor page `install_manpage --force` backed
    up before overwriting, under `Config.backup_dir` -- or None when install
    found the destination empty and took no backup, distinct from "unknown".
    `checksum` is the sha256 hex digest taken from the source file before
    the durable target that installed it -- materialization is byte-identical,
    so it is also the target's digest.
    `version` is the tool version the page documents, None where nothing
    was known to record -- including every entry written before this field
    existed (ADR-0018).
    `source_uri` is the exact upstream file or release asset URI for a
    repository-tier page; older entries and other tiers leave it None.
    `target` is the expected target of the owned manpath symlink.  It is
    absent only on entries written before ADR-0028 that migration could not
    safely convert.
    """

    path: Path
    tier: Tier
    source: str
    checksum: str
    backup: Path | None = None
    version: str | None = None
    source_uri: str | None = None
    target: Path | None = None


def checksum_of(path: str | Path) -> str:
    """Return the sha256 hex digest of a file's contents."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_path(config: Config | None) -> Path:
    return (config or Config()).manifest_path


def _entry_to_row(entry: Entry) -> dict[str, Any]:
    return {
        "path": str(entry.path),
        "tier": entry.tier.value,
        "source": entry.source,
        "checksum": entry.checksum,
        "backup": str(entry.backup) if entry.backup is not None else None,
        "version": entry.version,
        "source_uri": entry.source_uri,
        "target": str(entry.target) if entry.target is not None else None,
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
        raw_source_uri = row.get("source_uri")
        raw_target = row.get("target")
        source_uri = (
            raw_source_uri
            if isinstance(raw_source_uri, str)
            and raw_source_uri.startswith(("https://", "http://", "file://"))
            else None
        )
        return Entry(
            path=Path(row["path"]),
            tier=Tier(row["tier"]),
            source=row["source"],
            checksum=row["checksum"],
            backup=Path(backup) if backup is not None else None,
            # .get, not []: absent on every row written before this field
            # existed (ADR-0018), and that must read as None, not fail to parse.
            version=row.get("version"),
            source_uri=source_uri,
            target=Path(raw_target) if isinstance(raw_target, str) else None,
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
            path=item["path"],
            tier=Tier.SYNTHESIS,
            source=item["model"],
            checksum=checksum_of(item["path"]),
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
        # shutil.move, not Path.rename: man_dir (XDG_DATA_HOME) and
        # backup_dir (XDG_STATE_HOME) can be separate mounts, where a bare
        # rename raises EXDEV.
        shutil.move(backup_file, destination)
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
    if _migrate_links(entries, config):
        _save(path, entries)
    return entries


def _migrate_links(entries: dict[str, Entry], config: Config | None) -> bool:
    """Convert safely recoverable pre-ADR-0028 copies into owned links.

    The installed copy is the only trustworthy bytes source for an older
    entry.  It is materialized under MANIAC's durable output directory before
    replacing the manpath path.  A changed, missing, or pre-existing-link
    entry is deliberately retained: guessing at its provenance could destroy
    a usable user page.
    """
    cfg = config or Config()
    migrated = False
    for tool, entry in entries.items():
        if entry.target is not None:
            continue
        path = entry.path
        if path.is_symlink() or not path.is_file():
            logger.warning(
                "Retained unsafe legacy manpage entry", tool=tool, path=str(path)
            )
            continue
        try:
            if checksum_of(path) != entry.checksum:
                logger.warning(
                    "Retained changed legacy manpage entry", tool=tool, path=str(path)
                )
                continue
            target = cfg.output_dir / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary_target = target.with_name(f".{target.name}.tmp")
            shutil.copy2(path, temporary_target)
            temporary_target.replace(target)
            temporary_link = path.with_name(f".{path.name}.maniac.tmp")
            temporary_link.symlink_to(target)
            temporary_link.replace(path)
        except OSError as error:
            logger.warning(
                "Retained legacy manpage entry after migration failure",
                tool=tool,
                path=str(path),
                error=str(error),
            )
            continue
        entries[tool] = replace(entry, target=target)
        migrated = True
    return migrated


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
    checksum: str,
    backup: Path | None = None,
    config: Config | None = None,
    *,
    version: str | None = None,
    source_uri: str | None = None,
    target: Path | None = None,
) -> None:
    """Record `tool`'s installed page and its expected manpath-link target."""
    manifest_path = _manifest_path(config)
    entries = load(config)
    entries[tool] = Entry(
        path=Path(path),
        tier=tier,
        source=source,
        checksum=checksum,
        backup=backup,
        version=version,
        source_uri=source_uri,
        target=target,
    )
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
