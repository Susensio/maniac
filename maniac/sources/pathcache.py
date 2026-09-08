"""Shared cache for `Path.resolve()`, reused across every provider's `detect()`.

`_detect_via_registry` (`discovery.py`) tries providers in order until one
claims a binary; for a binary none of them claims -- most of a real `$PATH`
-- every provider runs, each walking the same symlink chain independently.
Memoizing here means that walk happens once per unique path per process,
not once per provider.
"""

from functools import cache
from pathlib import Path


@cache
def resolve_cached(path: Path) -> Path:
    """`path.resolve()`, memoized for the life of the process."""
    return path.resolve()
