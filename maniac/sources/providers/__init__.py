"""Installer providers: one module per installer, none calling another (ADR-0015).

Registers mise, uv tools and `~/.local/lib` on import, in the order the prior
`discovery._resolve_symlink_target` checked them (local_lib, uv, mise) --
their path markers are mutually exclusive, so order has no effect on which
one claims a given binary, only on the diff staying a pure refactor.

Stage 4 appends npm global, pipx, cargo, go and Homebrew after those three.
Registry order settles nothing between them either -- ADR-0015 breaks a tie
between providers by `$PATH` order, not registry order -- npm/pipx/cargo/go
are grouped by the table order the roadmap lists them in, Homebrew last as
the one with the least evidence behind it (unverified on the development
system, see docs/BACKLOG.md).
"""

from .base import Provider
from .cargo import CargoProvider
from .go import GoProvider
from .homebrew import HomebrewProvider
from .local_lib import LocalLibProvider
from .mise import MiseProvider
from .npm import NpmProvider
from .pipx import PipxProvider
from .registry import ProviderRegistry, registry
from .uv import UvProvider

registry.register(LocalLibProvider())
registry.register(UvProvider())
registry.register(MiseProvider())
registry.register(NpmProvider())
registry.register(PipxProvider())
registry.register(CargoProvider())
registry.register(GoProvider())
registry.register(HomebrewProvider())

__all__ = ["Provider", "ProviderRegistry", "registry"]
