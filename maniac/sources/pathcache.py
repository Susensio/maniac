"""Path resolution shared by every provider, depending on no provider itself.

`_detect_via_registry` (`resolution.py`) tries providers in order until one
claims a binary; for a binary none of them claims -- most of a real `$PATH`
-- every provider runs, each walking the same symlink chain independently.
Memoizing `resolve_cached` here means that walk happens once per unique path
per process, not once per provider.

`resolve_bin_path` lives here rather than beside the registry so that naming
a binary needs nothing from the provider layer: providers and the modules
they import can both reach it without an upward import.
"""

import os
from functools import cache
from pathlib import Path


@cache
def resolve_cached(path: Path) -> Path:
    """`path.resolve()`, memoized for the life of the process."""
    return path.resolve()


def path_dirs() -> list[Path]:
    """Directories on the inherited `$PATH`, in order, empty entries dropped (ADR-0061)."""
    return [
        Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry
    ]


def which(binary_name: str) -> Path | None:
    """First executable, non-directory match for `binary_name` across `path_dirs()`.

    Stops at the first hit rather than falling through to a later `$PATH`
    entry when it is unclaimed by any provider -- ADR-0020 rejects that
    fallthrough specifically: "unclaimed" cannot distinguish a transparent
    wrapper from a genuinely different build that shadows a managed one, so
    trying the next entry would attribute one binary's documentation to
    another with no evidence for it.
    """
    for directory in path_dirs():
        candidate = directory / binary_name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def resolve_bin_path(binary_name: str, bin_dir: str | Path | None) -> Path | None:
    """Locate a binary's path: an explicit directory first, then the inherited `$PATH`.

    With no `bin_dir`, resolution is exactly `which` -- nothing else.
    `enumerate_installations` applies the identical first-`$PATH`-entry-wins
    rule in bulk, by walking `$PATH` itself once for every name rather than
    calling `which` once per name; that walk, not a second notion of "which
    binary a name means", is the only reason the mechanics differ here.

    An explicit `bin_dir` is stated intent -- a directory the caller named,
    not one MANIAC found -- and is checked first regardless of what `$PATH`
    would have resolved to.
    """
    if bin_dir is not None:
        explicit_path = Path(bin_dir) / binary_name
        if explicit_path.exists():
            return explicit_path
    return which(binary_name)
