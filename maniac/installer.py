"""Manpage system installation, conflict resolution, and uninstallation.

Both write paths reconcile through `lifecycle` first, so an entry recorded
under an older storage policy is migrated before ownership is judged.
"""

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
    """
    src = Path(source_file)
    if durable_source and tier is not Tier.INSTALL_ROOT:
        raise ValueError("Only install-root pages may link directly to their source")
    if provider_target and not durable_source:
        raise ValueError("A provider target must link directly to its source")
    checksum = manifest.checksum_of(src)
    cfg = config or Config()
    dest_dir = Path(target_dir).expanduser() if target_dir else cfg.man_dir
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_file = dest_dir / src.name

    entries = lifecycle.reconcile(cfg)

    backup_path: Path | None = None
    previous_entry: Entry | None = None
    if _path_exists(dest_file):
        existing = entries.get(tool)
        owned = (
            existing is not None
            and existing.path == dest_file
            and manifest.is_expected_link(existing)
        )
        if owned:
            # Reinstalling over our own page: carry the prior backup forward
            # rather than dropping it, or a vendor page backed up on an
            # earlier `--force` install becomes unrestorable on uninstall.
            assert existing is not None
            backup_path = existing.backup
            previous_entry = existing
        else:
            if not force:
                raise FileExistsError(
                    f"A foreign or vendor manpage already exists at '{dest_file}'. "
                    f"Use --force to create a backup and overwrite."
                )
            backup_dir = cfg.backup_dir
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / dest_file.name
            shutil.copy2(dest_file, backup_path)
            logger.info(
                "Created backup of foreign manpage", backup_file=str(backup_path)
            )

    try:
        target = lifecycle.materialize_target(
            src, cfg, durable_source=durable_source, entries=entries, tool=tool
        )
        lifecycle.link_manpath_entry(dest_file, target)
    except Exception:
        if previous_entry is not None:
            # A reinstall over our own page failed mid-copy: restore the
            # prior entry rather than forgetting it outright, or its
            # vendor backup (still on disk, still valid) becomes orphaned.
            # previous_entry.version, not the new `version` param: the copy
            # never happened, so the old page (and the version it documents)
            # is still what's on disk.
            manifest.record(
                tool,
                previous_entry.path,
                previous_entry.tier,
                previous_entry.source,
                previous_entry.checksum,
                backup=previous_entry.backup,
                version=previous_entry.version,
                source_uri=previous_entry.source_uri,
                target=previous_entry.target,
                provider_target=previous_entry.provider_target,
                group=previous_entry.group,
                config=cfg,
            )
        else:
            manifest.forget(tool, config=cfg)
        raise
    manifest.record(
        tool,
        dest_file,
        tier,
        source,
        checksum,
        backup=backup_path,
        version=version,
        source_uri=source_uri,
        target=target,
        provider_target=provider_target,
        group=group,
        config=cfg,
    )
    logger.info("Installed manpage", path=str(dest_file))
    return dest_file


def _path_exists(path: Path) -> bool:
    """Whether a path exists, including a dangling symlink."""
    return path.exists() or path.is_symlink()


class KeptReason(Enum):
    """Why a recorded page survived an uninstall."""

    # Pre-ADR-0028 entry carrying no recorded target, so nothing to verify
    # its bytes against. Not a modification: the page may be untouched.
    LEGACY = "legacy"
    # Recorded target present but the page no longer matches what was
    # installed -- edited, replaced, retargeted or left dangling.
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
    # Every group member whose manifest entry exists but whose page's bytes no
    # longer match the checksum taken at install: ours, but changed since, so
    # left in place unless `--force` overrides the check. A list because one
    # uninstall covers a whole upstream release and each member is checked
    # on its own.
    modified_kept: list[Path] = field(default_factory=list)
    # Every group member predating ADR-0028's symlink tracking whose migration
    # never succeeded. Nothing was edited; there is simply no recorded target
    # to check, and only `--force` authorizes removing it.
    legacy_kept: list[Path] = field(default_factory=list)


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


def _kept_reason(entry: Entry, *, force: bool) -> KeptReason | None:
    """Why a manifest entry is no longer safe to remove, or None when it is.

    `force` is consulted before the missing-target check, not after: a
    pre-ADR-0028 entry whose migration failed keeps `target=None` forever
    (`lifecycle._migrate_links` retains it on OSError), so checking first
    made such an entry permanently un-uninstallable with no override.
    A replaced, retargeted or dangling entry still outranks `force`,
    because the page occupying the manpath is then not the one recorded.

    A targetless entry whose page's bytes no longer match what was recorded
    is still MODIFIED -- the bytes are the only evidence such an entry has.
    """
    if entry.target is None:
        if force:
            return None
        return (
            KeptReason.LEGACY if _matches_recorded_bytes(entry) else KeptReason.MODIFIED
        )
    if not manifest.is_expected_link(entry):
        return KeptReason.MODIFIED
    if force or entry.provider_target:
        return None
    target = manifest.expected_target_path(entry)
    if manifest.checksum_of(target) != entry.checksum:
        return KeptReason.MODIFIED
    return None


def _remove_recorded_manpage(
    tool_name: str,
    cfg: Config,
    *,
    entries: dict[str, Entry],
    force: bool,
    removed_paths: list[Path],
) -> tuple[Entry | None, Path | None, KeptReason | None]:
    """Remove a recorded link, returning its entry and why any page was kept.

    `entries` is the reconciled manifest and is mutated as records are
    forgotten, so a later member of the same group sees what the earlier
    ones already removed -- which is what decides whether a shared durable
    target still has a user.
    """
    entry = entries.get(tool_name)
    if entry is None:
        return None, _foreign_manpage(tool_name, cfg), None
    if not _path_exists(entry.path):
        _forget(tool_name, entries, cfg)
        return entry, None, None
    kept = _kept_reason(entry, force=force)
    if kept is not None:
        return entry, None, kept

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
    _forget(tool_name, entries, cfg)

    discarded = lifecycle.discard_durable_target(entry, cfg, entries)
    if discarded is not None:
        removed_paths.append(discarded)
    return entry, None, None


def _forget(tool_name: str, entries: dict[str, Entry], cfg: Config) -> None:
    """Drop a tool's record from the manifest and from the reconciled snapshot."""
    entries.pop(tool_name, None)
    manifest.forget(tool_name, config=cfg)


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
        cfg.intermediate_dir / f"{tool_name}_prompt.md",
    )
    for path in paths:
        if path.exists():
            path.unlink()
            removed_paths.append(path)


def uninstall_manpage(
    tool_name: str,
    purge: bool = False,
    force: bool = False,
    config: Config | None = None,
) -> UninstallResult:
    """Uninstall a MANIAC-generated manpage and restore backups if present.

    Uninstall operates on the whole upstream release, not one page: every
    entry sharing `tool_name`'s group goes, each checksum-protected before
    removal and each restoring its own displaced vendor backup.

    A recorded page whose current bytes no longer match the checksum taken
    at install is left in place unless `force` overrides the check,
    mirroring `install_manpage`'s own `--force` -- but reported via
    `modified_kept`, not `foreign_kept`: the manifest entry proves MANIAC
    installed it (ADR-0017), so it is ours, only changed since. An entry
    predating ADR-0028's symlink tracking has no recorded target to check
    at all, so it is reported via `legacy_kept` instead, and `force`
    removes it. A page missing entirely is not a mismatch: `install_manpage`
    records after materializing and linking, so a crash between the two
    leaves an entry whose page never arrived -- forgotten, not flagged.
    """
    cfg = config or Config()
    removed_paths: list[Path] = []
    # removed_paths, not a bare reconcile: ADR-0032's migration can delete
    # the superseded durable copy itself, and uninstall reports every path
    # it removed.
    entries = lifecycle.reconcile(cfg, removed=removed_paths)

    foreign_kept: Path | None = None
    modified_kept: list[Path] = []
    legacy_kept: list[Path] = []
    for member in _group_members(tool_name, entries):
        # 1. Active installed manpage, wherever the manifest says MANIAC put
        # it -- the manifest's recorded path, not a `<tool>.1` guess, is what
        # closes the compressed-page hole (a tier-1 `pandoc.1.gz` was
        # previously unreachable here) (ADR-0017).
        entry, member_foreign, member_kept = _remove_recorded_manpage(
            member,
            cfg,
            entries=entries,
            force=force,
            removed_paths=removed_paths,
        )
        if member_foreign is not None:
            foreign_kept = member_foreign
        if member_kept is not None:
            assert entry is not None
            kept_pages = (
                legacy_kept if member_kept is KeptReason.LEGACY else modified_kept
            )
            kept_pages.append(entry.path)

        # 2. XDG data storage (output_dir / <tool>.1)
        _remove_orphaned_roff(member, cfg, entry, removed_paths)

        if purge:
            _purge_artifacts(member, cfg, removed_paths)

    return UninstallResult(
        removed=removed_paths,
        foreign_kept=foreign_kept,
        modified_kept=modified_kept,
        legacy_kept=legacy_kept,
    )
