"""Track which pages MANIAC installed (ADR-0017, ADR-0028).

The manifest, not a page's own bytes, is what `install`, `uninstall` and
`status` consult to decide ownership.  Its recorded target establishes the
expected manpath link; a provenance header on tier-3 pages is informational.
"""

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .config import Config

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
    `provider_target` marks a validated provider-managed target whose bytes
    may advance independently of MANIAC.
    """

    path: Path
    tier: Tier
    source: str
    checksum: str
    backup: Path | None = None
    version: str | None = None
    source_uri: str | None = None
    target: Path | None = None
    provider_target: bool = False


def checksum_of(path: str | Path) -> str:
    """Return the sha256 hex digest of a file's contents."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_target_path(entry: Entry) -> Path:
    """Return a recorded target resolved from its manpath entry."""
    assert entry.target is not None
    return (
        entry.target if entry.target.is_absolute() else entry.path.parent / entry.target
    )


def is_expected_link(entry: Entry) -> bool:
    """Whether a manpath entry still points literally to its recorded target."""
    if entry.target is None or not entry.path.is_symlink():
        return False
    try:
        if entry.path.readlink() != entry.target:
            return False
    except OSError:
        return False
    return expected_target_path(entry).exists()


def is_maniac_owned_target(target: Path | None, config: Config) -> bool:
    """Whether a target belongs to MANIAC's durable output storage."""
    if target is None:
        return False
    try:
        target.absolute().relative_to(config.output_dir.absolute())
    except ValueError:
        return False
    return True


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
        "provider_target": entry.provider_target,
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
            provider_target=row.get("provider_target") is True,
        )
    except (KeyError, TypeError, ValueError):
        return None


def load(config: Config | None = None) -> dict[str, Entry]:
    """Return every recorded entry, keyed by tool.

    Pure deserialization: an absent manifest reads empty, and nothing here
    touches the filesystem beyond reading the manifest file, so a read-only
    command sees ownership exactly as persisted. Seeding and link
    reconciliation belong to `lifecycle.reconcile`, which write paths call.

    A manifest that exists but fails to parse -- corrupt JSON, wrong shape,
    an unrecognized version -- degrades to empty instead of raising; a
    corrupt store costs a rebuild, never a crash.
    """
    path = _manifest_path(config)
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


def save(entries: dict[str, Entry], config: Config | None = None) -> None:
    """Persist `entries` as the whole manifest, replacing what it held."""
    path = _manifest_path(config)
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
    provider_target: bool = False,
) -> None:
    """Record `tool`'s installed page and its expected manpath-link target."""
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
        provider_target=provider_target,
    )
    save(entries, config)


def lookup(tool: str, config: Config | None = None) -> Entry | None:
    """Return `tool`'s recorded entry, or None if MANIAC never installed it."""
    return load(config).get(tool)


def forget(tool: str, config: Config | None = None) -> None:
    """Remove `tool`'s entry, if any. A no-op if it was never recorded."""
    entries = load(config)
    if tool in entries:
        del entries[tool]
        save(entries, config)
