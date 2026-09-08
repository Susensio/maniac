"""Installer providers: one module per installer, none calling another (ADR-0015).

Registers mise, uv tools and `~/.local/lib` on import, in the order the prior
`discovery._resolve_symlink_target` checked them (local_lib, uv, mise) --
their path markers are mutually exclusive, so order has no effect on which
one claims a given binary, only on the diff staying a pure refactor.
"""

from .base import Provider
from .local_lib import LocalLibProvider
from .mise import MiseProvider
from .registry import ProviderRegistry, registry
from .uv import UvProvider

registry.register(LocalLibProvider())
registry.register(UvProvider())
registry.register(MiseProvider())

__all__ = ["Provider", "ProviderRegistry", "registry"]
