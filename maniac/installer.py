"""Manpage system installation, conflict resolution, and uninstallation."""

import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from . import lifecycle, manifest
from .config import Config
from .logging import logger
from .manifest import Entry, Tier


def install_manpage(
    source_file: str | Path,
    tool: str,
    tier: Tier,
    source: str,
    target_dir: str | Path | None = None,
    force: bool = False,
    *,
    version: str | None = None,
    source_uri: str | None = None,
    durable_source: bool = False,
    provider_target: bool = False,
    group: str | None = None,
    transaction: manifest.Transaction | None = None,
    config: Config | None = None,
) -> Path:
    """Link a manpath entry to a durable page with conflict guard and backup.

    Ownership of an occupying page is decided by the manifest (ADR-0017),
    never by its bytes: `tool`'s existing entry pointing at this exact
    destination means MANIAC put it there and it is safe to overwrite
    without `--force`; anything else is foreign and still needs `--force`,
    which backs it up into `Config.backup_dir` rather than `man_dir` -- a
    non-manpage file has no business in a directory `man`/`mandb` scan.

    `group` is the manifest key of the primary page of the upstream release
    this page came in; every page of one multi-page release is installed
    with the same value, which is what makes them uninstall together.

    `transaction` joins a manifest transaction the caller already opened,
    which is what makes such a release atomic: every page records into the
    one working set and the whole group lands in a single write, or none of
    it does (ADR-0046).  It carries the config for the install, so a caller
    passing it passes no `config`.
    """
    src = Path(source_file)
    if durable_source and tier is not Tier.INSTALL_ROOT:
        raise ValueError("Only install-root pages may link directly to their source")
    if provider_target and not durable_source:
        raise ValueError("A provider target must link directly to its source")
    checksum = manifest.checksum_of(src)
    cfg = transaction.config if transaction is not None else config or Config()
    dest_dir = Path(target_dir).expanduser() if target_dir else cfg.man_dir
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_file = dest_dir / src.name

    # The generate phase is over by here -- `src` exists. Everything below is
    # backup, link and record, which is milliseconds, so it is the whole of
    # what the lock spans (ADR-0043).
    with manifest.joined(transaction, cfg) as txn:
        entries = txn.entries
        # Ownership read before the duplicates go, or the backup the owning
        # record carries is dropped with it.
        owner = _owner_of(dest_file, entries)
        _drop_other_records_of(dest_file, txn, tool=tool)
        backup_path, fresh_backup = _take_backup(dest_file, owner, cfg, force=force)
        target = _materialize_and_link(
            src,
            dest_file,
            cfg,
            entries=entries,
            tool=tool,
            durable_source=durable_source,
            backup_path=backup_path if fresh_backup else None,
        )
        txn.put(
            tool,
            Entry(
                path=dest_file,
                tier=tier,
                source=source,
                checksum=checksum,
                backup=backup_path,
                version=version,
                source_uri=source_uri,
                target=target,
                provider_target=provider_target,
                group=group,
            ),
        )
    logger.info("Installed manpage", path=str(dest_file))
    return dest_file


def _owner_of(dest_file: Path, entries: dict[str, Entry]) -> Entry | None:
    """Return the entry whose sound link occupies `dest_file`, whatever key holds it.

    By path, not by tool key: an orphan adopted after a crash is keyed on
    its filename, and the page is still MANIAC's (ADR-0017).
    """
    return next(
        (
            entry
            for entry in entries.values()
            if entry.path == dest_file and manifest.is_expected_link(entry)
        ),
        None,
    )


def _drop_other_records_of(
    dest_file: Path, txn: manifest.Transaction, *, tool: str
) -> None:
    """Forget every other entry recording `dest_file`: one manpath path, one owner."""
    for other, entry in list(txn.entries.items()):
        if other != tool and entry.path == dest_file:
            txn.forget(other)
            logger.info(
                "Dropped a duplicate record of a manpath entry",
                tool=other,
                path=str(dest_file),
            )


def _take_backup(
    dest_file: Path,
    existing: Entry | None,
    cfg: Config,
    *,
    force: bool,
) -> tuple[Path | None, bool]:
    """Return the backup to record and whether this call created it.

    Reinstalling over a page MANIAC already owns carries the prior backup
    forward rather than dropping it, or a vendor page backed up on an
    earlier `--force` install becomes unrestorable on uninstall.  A fresh
    backup is named after the manpath entry it displaced, which is what
    makes an orphaned one traceable to the page it must be restored over.
    """
    if not _path_exists(dest_file):
        return None, False
    if existing is not None:
        return existing.backup, False
    if not force:
        raise FileExistsError(
            f"A foreign or vendor manpage already exists at '{dest_file}'. "
            f"Use --force to create a backup and overwrite."
        )
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = cfg.backup_dir / dest_file.name
    shutil.copy2(dest_file, backup_path)
    logger.info("Created backup of foreign manpage", backup_file=str(backup_path))
    return backup_path, True


def _materialize_and_link(
    src: Path,
    dest_file: Path,
    cfg: Config,
    *,
    entries: dict[str, Entry],
    tool: str,
    durable_source: bool,
    backup_path: Path | None,
) -> Path:
    """Return the linked target, undoing every partial step if one of them fails.

    A failure here leaves nothing behind: the transaction discards the
    manifest side, and this discards the filesystem side -- the durable
    target this call wrote, and the backup this call took of a page that is
    consequently still in place.
    """
    materialized: lifecycle.Materialized | None = None
    try:
        materialized = lifecycle.materialize_target(
            src, cfg, durable_source=durable_source, entries=entries, tool=tool
        )
        lifecycle.link_manpath_entry(dest_file, materialized.path)
    except Exception:
        _discard_materialized_target(materialized, cfg, entries)
        if backup_path is not None:
            _restore_or_discard_backup(backup_path, dest_file)
        raise
    return materialized.path


def _discard_materialized_target(
    materialized: lifecycle.Materialized | None,
    cfg: Config,
    entries: dict[str, Entry],
) -> None:
    """Remove a durable target this call wrote and no entry records.

    Only one this call wrote: a tier-3 source already sits at its durable
    path, so the failed install created nothing, and unlinking there would
    throw away the synthesized page a paid LLM call produced.
    A recorded target is another install's, or this tool's own previous one:
    unlinking it would dangle a link that is still correct.
    """
    if materialized is None or not materialized.copied:
        return
    target = materialized.path
    if lifecycle.target_users(entries, cfg).get(target.absolute()):
        return
    target.unlink(missing_ok=True)


def _restore_or_discard_backup(backup_path: Path, dest_file: Path) -> None:
    """Put a failed install's backup back, or drop it if its page never moved."""
    if _path_exists(dest_file):
        backup_path.unlink(missing_ok=True)
        return
    shutil.move(backup_path, dest_file)
    logger.info("Restored displaced manpage after failed install", path=str(dest_file))


def _path_exists(path: Path) -> bool:
    """Whether a path exists, including a dangling symlink."""
    return path.exists() or path.is_symlink()


class KeptReason(Enum):
    """Why a recorded page survived an uninstall."""

    # Recorded target present but the link no longer points where
    # installation left it -- retargeted or dangling. Not provably MANIAC's
    # occupying page anymore, so `force` cannot reach it either.
    MODIFIED = "modified"


@dataclass
class UninstallResult:
    """Outcome of an `uninstall_manpage()` call."""

    removed: list[Path] = field(default_factory=list)
    # Set when no manifest entry names this tool: MANIAC never installed the
    # occupying page, so it was left in place. Distinct from `modified_kept`
    # below -- conflating the two told the caller a page MANIAC did install
    # was foreign, which it was not.
    foreign_kept: Path | None = None
    # Every group member whose manpath link no longer points where
    # installation left it -- retargeted or dangling. Left in place: the
    # link no longer provably points to MANIAC's page at all, so there is
    # nothing here checksums could vouch for either way.
    modified_kept: list[Path] = field(default_factory=list)
    # Every group member removed despite its bytes no longer matching what
    # was recorded at install -- edited after the fact, or (pre-ADR-0028)
    # with no recorded target to check bytes against in the first place.
    # The manifest entry, not the page's current bytes, is what proves
    # MANIAC's ownership (ADR-0017), so this is a warning, not a refusal.
    changed: list[Path] = field(default_factory=list)


def _foreign_manpage(tool_name: str, cfg: Config) -> Path | None:
    """Return an unrecorded uncompressed manpath page, if present."""
    installed_file = cfg.man_dir / f"{tool_name}.1"
    return installed_file if installed_file.exists() else None


def _matches_recorded_bytes(entry: Entry) -> bool:
    """Whether a targetless entry's page still holds the bytes recorded at install.

    A symlink or non-regular file counts as a mismatch: the recorded bytes
    were a copy, so anything else occupying the path is not them.
    """
    if entry.path.is_symlink() or not entry.path.is_file():
        return False
    try:
        return manifest.checksum_of(entry.path) == entry.checksum
    except OSError:
        return False


def _kept_reason(entry: Entry) -> KeptReason | None:
    """Whether `entry`'s manpath link no longer provably points to MANIAC's page.

    A targetless (pre-ADR-0028) entry has nothing to compare a link
    against, so it is never kept for that alone -- removed, with a bytes
    mismatch reported via `_bytes_changed` instead. A retargeted or
    dangling link is the one case still kept: the page occupying the
    manpath, or failing to, is then not provably the one MANIAC recorded.
    """
    if entry.target is None:
        return None
    if not manifest.is_expected_link(entry):
        return KeptReason.MODIFIED
    return None


def _bytes_changed(entry: Entry) -> bool:
    """Whether the page's bytes no longer match what was recorded at install.

    Only meaningful once `_kept_reason` has already let the entry through:
    a retargeted or dangling link is reported there, not here. Never true
    for a provider-owned target -- that file is the provider's, not a
    durable copy MANIAC's checksum can judge.
    """
    if entry.target is None:
        return not _matches_recorded_bytes(entry)
    if entry.provider_target:
        return False
    target = manifest.expected_target_path(entry)
    return manifest.checksum_of(target) != entry.checksum


def _remove_recorded_manpage(
    tool_name: str,
    txn: manifest.Transaction,
    *,
    removed_paths: list[Path],
    changed_paths: list[Path],
) -> tuple[Entry | None, Path | None, KeptReason | None]:
    """Remove a recorded link, returning its entry and why any page was kept.

    The transaction's entries are mutated as records are forgotten, so a
    later member of the same group sees what the earlier ones already
    removed -- which is what decides whether a shared durable target still
    has a user.  All of it lands in one write when the whole group is done.
    """
    cfg = txn.config
    entries = txn.entries
    entry = entries.get(tool_name)
    if entry is None:
        return None, _foreign_manpage(tool_name, cfg), None
    if not _path_exists(entry.path):
        txn.forget(tool_name)
        return entry, None, None
    kept = _kept_reason(entry)
    if kept is not None:
        return entry, None, kept
    if _bytes_changed(entry):
        changed_paths.append(entry.path)

    installed_file = entry.path
    installed_file.unlink()
    logger.info("Removed installed manpage", path=str(installed_file))
    if entry.backup is not None and entry.backup.exists():
        # shutil.move, not Path.rename: the backup lives under
        # Config.backup_dir (XDG_STATE_HOME), the page under man_dir
        # (XDG_DATA_HOME) -- separate mounts raise EXDEV on a bare rename.
        shutil.move(entry.backup, installed_file)
        logger.info("Restored vendor backup manpage", path=str(installed_file))
    else:
        removed_paths.append(installed_file)
    txn.forget(tool_name)

    discarded = lifecycle.discard_durable_target(entry, cfg, entries)
    if discarded is not None:
        removed_paths.append(discarded)
    return entry, None, None


def _group_members(tool_name: str, entries: dict[str, Entry]) -> list[str]:
    """Return every manifest key uninstalling `tool_name` must reach, primary first.

    Pages installed from one upstream release carry that release's primary
    key in `group`, so any member reaches the whole unit -- uninstalling a
    companion and uninstalling the primary name the same set.  A tool with
    no entry, or an entry recorded before groups existed, is its own sole
    member.
    """
    entry = entries.get(tool_name)
    if entry is None or entry.group is None:
        return [tool_name]
    companions = sorted(
        member
        for member, other in entries.items()
        if other.group == entry.group and member != entry.group
    )
    primary = [entry.group] if entry.group in entries else []
    return primary + companions


def _remove_orphaned_roff(
    tool_name: str, cfg: Config, entry: Entry | None, removed_paths: list[Path]
) -> None:
    """Remove the legacy durable roff copy when it is not the recorded target."""
    stored_roff = cfg.output_dir / f"{tool_name}.1"
    if stored_roff.exists() and (entry is None or stored_roff != entry.target):
        stored_roff.unlink()
        removed_paths.append(stored_roff)


def _purge_artifacts(tool_name: str, cfg: Config, removed_paths: list[Path]) -> None:
    """Remove optional generated sources and intermediates for a tool."""
    paths = (
        cfg.output_dir / f"{tool_name}.1.md",
        cfg.intermediate_dir / f"{tool_name}_context.md",
    )
    for path in paths:
        if path.exists():
            path.unlink()
            removed_paths.append(path)


def uninstall_manpage(
    tool_name: str,
    purge: bool = False,
    config: Config | None = None,
) -> UninstallResult:
    """Uninstall a MANIAC-generated manpage and restore backups if present.

    Uninstall operates on the whole upstream release, not one page: every
    entry sharing `tool_name`'s group goes, each restoring its own displaced
    vendor backup.

    A recorded page is removed even when its current bytes no longer match
    the checksum taken at install -- the manifest entry proves MANIAC
    installed it (ADR-0017), so it is ours regardless -- but reported via
    `changed`, a warning rather than a refusal. A retargeted or dangling
    link is different: the manpath no longer provably points to MANIAC's
    page at all, so it is left in place and reported via `modified_kept`
    instead. A page missing entirely is not a mismatch: `install_manpage`
    records after materializing and linking, so a crash between the two
    leaves an entry whose page never arrived -- forgotten, not flagged.
    """
    cfg = config or Config()
    removed_paths: list[Path] = []
    with manifest.transaction(cfg) as txn:
        foreign_kept, modified_kept, changed = _uninstall_group(
            tool_name, txn, purge=purge, removed_paths=removed_paths
        )
    return UninstallResult(
        removed=removed_paths,
        foreign_kept=foreign_kept,
        modified_kept=modified_kept,
        changed=changed,
    )


def _uninstall_group(
    tool_name: str,
    txn: manifest.Transaction,
    *,
    purge: bool,
    removed_paths: list[Path],
) -> tuple[Path | None, list[Path], list[Path]]:
    """Remove every member of `tool_name`'s release, reporting what was kept."""
    cfg = txn.config
    foreign_kept: Path | None = None
    modified_kept: list[Path] = []
    changed: list[Path] = []
    for member in _group_members(tool_name, txn.entries):
        # 1. Active installed manpage, wherever the manifest says MANIAC put
        # it -- the manifest's recorded path, not a `<tool>.1` guess, is what
        # closes the compressed-page hole (a tier-1 `pandoc.1.gz` was
        # previously unreachable here) (ADR-0017).
        entry, member_foreign, member_kept = _remove_recorded_manpage(
            member,
            txn,
            removed_paths=removed_paths,
            changed_paths=changed,
        )
        if member_foreign is not None:
            foreign_kept = member_foreign
        if member_kept is not None:
            assert entry is not None
            modified_kept.append(entry.path)

        # 2. XDG data storage (output_dir / <tool>.1)
        _remove_orphaned_roff(member, cfg, entry, removed_paths)

        if purge:
            _purge_artifacts(member, cfg, removed_paths)

    return foreign_kept, modified_kept, changed
