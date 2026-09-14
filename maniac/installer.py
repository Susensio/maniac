"""Manpage system installation, conflict resolution, and uninstallation.

Both write paths reconcile through `lifecycle` first, so an entry recorded
under an older storage policy is migrated before ownership is judged.
"""

import shutil
from dataclasses import dataclass, field
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
    config: Config | None = None,
) -> Path:
    """Link a manpath entry to a durable page with conflict guard and backup.

    Ownership of an occupying page is decided by the manifest (ADR-0017),
    never by its bytes: `tool`'s existing entry pointing at this exact
    destination means MANIAC put it there and it is safe to overwrite
    without `--force`; anything else is foreign and still needs `--force`,
    which backs it up into `Config.backup_dir` rather than `man_dir` -- a
    non-manpage file has no business in a directory `man`/`mandb` scan.
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
        target = lifecycle.materialize_target(src, cfg, durable_source=durable_source)
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
        config=cfg,
    )
    logger.info("Installed manpage", path=str(dest_file))
    return dest_file


def _path_exists(path: Path) -> bool:
    """Whether a path exists, including a dangling symlink."""
    return path.exists() or path.is_symlink()


@dataclass
class UninstallResult:
    """Outcome of an `uninstall_manpage()` call."""

    removed: list[Path] = field(default_factory=list)
    # Set when no manifest entry names this tool: MANIAC never installed the
    # occupying page, so it was left in place. Distinct from `modified_kept`
    # below -- conflating the two told the caller a page MANIAC did install
    # was foreign, which it was not.
    foreign_kept: Path | None = None
    # Set when the manifest entry exists but the page's bytes no longer match
    # the checksum taken at install: ours, but changed since, so left in
    # place unless `--force` overrides the check.
    modified_kept: Path | None = None


def _foreign_manpage(tool_name: str, cfg: Config) -> Path | None:
    """Return an unrecorded uncompressed manpath page, if present."""
    installed_file = cfg.man_dir / f"{tool_name}.1"
    return installed_file if installed_file.exists() else None


def _entry_must_be_kept(entry: Entry, *, force: bool) -> bool:
    """Whether a manifest entry is no longer safe to remove."""
    if entry.target is None or not manifest.is_expected_link(entry):
        return True
    if force or entry.provider_target:
        return False
    target = manifest.expected_target_path(entry)
    return manifest.checksum_of(target) != entry.checksum


def _remove_recorded_manpage(
    tool_name: str,
    cfg: Config,
    *,
    entry: Entry | None,
    force: bool,
    removed_paths: list[Path],
) -> tuple[Entry | None, Path | None, Path | None]:
    """Remove a recorded link, returning its entry and any kept-page status."""
    if entry is None:
        return None, _foreign_manpage(tool_name, cfg), None
    if not _path_exists(entry.path):
        manifest.forget(tool_name, config=cfg)
        return entry, None, None
    if _entry_must_be_kept(entry, force=force):
        return entry, None, entry.path

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
    manifest.forget(tool_name, config=cfg)

    discarded = lifecycle.discard_durable_target(entry, cfg)
    if discarded is not None:
        removed_paths.append(discarded)
    return entry, None, None


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

    A recorded page whose current bytes no longer match the checksum taken
    at install is left in place unless `force` overrides the check,
    mirroring `install_manpage`'s own `--force` -- but reported via
    `modified_kept`, not `foreign_kept`: the manifest entry proves MANIAC
    installed it (ADR-0017), so it is ours, only changed since. A page
    missing entirely is not a mismatch: it is the crash window the
    entry-before-copy ordering deliberately creates, so its entry is
    forgotten, not flagged.
    """
    cfg = config or Config()
    removed_paths: list[Path] = []
    # 1. Active installed manpage, wherever the manifest says MANIAC put it --
    # the manifest's recorded path, not a `<tool>.1` guess, is what closes
    # the compressed-page hole (a tier-1 `pandoc.1.gz` was previously
    # unreachable here) (ADR-0017).
    entry, foreign_kept, modified_kept = _remove_recorded_manpage(
        tool_name,
        cfg,
        entry=lifecycle.reconcile(cfg).get(tool_name),
        force=force,
        removed_paths=removed_paths,
    )

    # 2. XDG data storage (output_dir / <tool>.1)
    _remove_orphaned_roff(tool_name, cfg, entry, removed_paths)

    if purge:
        _purge_artifacts(tool_name, cfg, removed_paths)

    return UninstallResult(
        removed=removed_paths, foreign_kept=foreign_kept, modified_kept=modified_kept
    )
