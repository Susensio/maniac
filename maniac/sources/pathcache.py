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

from functools import cache
from pathlib import Path

from . import loginpath


@cache
def resolve_cached(path: Path) -> Path:
    """`path.resolve()`, memoized for the life of the process."""
    return path.resolve()


def resolve_bin_path(binary_name: str, bin_dir: str | Path | None) -> Path | None:
    """Locate a binary's path: an explicit directory first, then the login `$PATH`.

    With no `bin_dir`, resolution is exactly `loginpath.which_login` --
    nothing else. `enumerate_installations` applies the identical first-
    `$PATH`-entry-wins rule in bulk, by walking the login `$PATH` itself
    once for every name rather than calling `which_login` once per name;
    that walk, not a second notion of "which binary a name means", is the
    only reason the mechanics differ here.

    An explicit `bin_dir` is stated intent -- a directory the caller named,
    not one MANIAC found -- and is checked first regardless of what the
    login `$PATH` would have resolved to.
    """
    if bin_dir is not None:
        explicit_path = Path(bin_dir) / binary_name
        if explicit_path.exists():
            return explicit_path
    return loginpath.which_login(binary_name)
