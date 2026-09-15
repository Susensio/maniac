"""Filesystem lifecycle of MANIAC-managed manpath entries (ADR-0028, ADR-0031, ADR-0032).

Every transition a managed page can undergo lives here: materializing
durable targets, replacing manpath links and removing superseded ones.
`manifest` only serializes; nothing in this module runs as an effect of
deserializing it.
"""

import shutil
from pathlib import Path

from . import manifest
from .config import Config
from .logging import logger
from .manifest import Entry


def materialize_target(
    src: Path,
    config: Config,
    *,
    durable_source: bool,
    entries: dict[str, Entry],
    tool: str,
) -> Path:
    """Return an upgrade-safe link target, materializing it before link replacement.

    The durable target is named after the source page, so two tools whose
    pages share a basename resolve to one path.  Writing it would replace a
    second entry's recorded bytes, and that entry's checksum would then
    mismatch forever -- leaving it un-uninstallable.  A collision is refused
    by name rather than renamed around: the durable name is derived, nothing
    reads a disambiguated one back, and a page arriving under two manifest
    keys is a mistake worth surfacing.
    """
    if durable_source:
        return src.absolute()
    target = config.output_dir / src.name
    owner = _recorded_target_owner(target, entries, config, tool)
    if owner is not None:
        raise FileExistsError(
            f"The durable target '{target}' is already recorded by '{owner}'. "
            f"Uninstall '{owner}' or rename the source page before installing "
            f"'{tool}'."
        )
    if src.absolute() == target.absolute():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_target = target.with_name(f".{target.name}.tmp")
    shutil.copy2(src, temporary_target)
    temporary_target.replace(target)
    return target


def target_users(entries: dict[str, Entry], config: Config) -> dict[Path, list[str]]:
    """Which tools record each MANIAC-owned durable target, keyed by absolute path.

    One durable target can carry more than one entry, so every decision to
    write or delete one asks this first.
    """
    users: dict[Path, list[str]] = {}
    for tool, entry in entries.items():
        if entry.target is None:
            continue
        target = manifest.expected_target_path(entry).absolute()
        if manifest.is_maniac_owned_target(target, config):
            users.setdefault(target, []).append(tool)
    return users


def _recorded_target_owner(
    target: Path, entries: dict[str, Entry], config: Config, tool: str
) -> str | None:
    """Name of another tool whose entry already records `target`, or None."""
    users = target_users(entries, config).get(target.absolute(), [])
    return next((other for other in users if other != tool), None)


def link_manpath_entry(path: Path, target: Path) -> None:
    """Point a manpath entry at `target`, replacing whatever occupies it atomically."""
    temporary_link = path.with_name(f".{path.name}.maniac.tmp")
    temporary_link.unlink(missing_ok=True)
    try:
        temporary_link.symlink_to(target)
        temporary_link.replace(path)
    except OSError:
        # A staged link nothing reached is invisible litter on the manpath;
        # `man` would still scan it.
        temporary_link.unlink(missing_ok=True)
        raise


def discard_durable_target(
    entry: Entry, config: Config, entries: dict[str, Entry]
) -> Path | None:
    """Remove the MANIAC-owned target an uninstalled entry leaves behind, if any.

    Returns the removed path, or None when nothing was MANIAC's to remove --
    a provider-owned target is never deleted (ADR-0031), and neither is one
    another entry in `entries` still records.  `entries` holds what remains
    after `entry`'s own record is forgotten; unlinking a shared target would
    dangle the other entry's link and leave its checksum unverifiable, so it
    could never be uninstalled either.
    """
    if entry.target is None or entry.provider_target:
        return None
    target = manifest.expected_target_path(entry)
    if not manifest.is_maniac_owned_target(target, config) or not target.exists():
        return None
    sharers = target_users(entries, config).get(target.absolute(), [])
    if sharers:
        logger.info(
            "Retained durable target still recorded elsewhere",
            path=str(target),
            tools=sharers,
        )
        return None
    target.unlink()
    return target
