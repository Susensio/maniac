"""Ordered provider registry and conservative path routing."""

from collections.abc import Iterator
from pathlib import Path

from ..pathcache import resolve_cached
from .base import Provider
from .cargo import CargoProvider
from .go import GoProvider
from .homebrew import HomebrewProvider
from .local_lib import LocalLibProvider
from .mise import MiseProvider
from .npm import NpmProvider
from .pipx import PipxProvider
from .uv import UvProvider

_SYSTEM_BIN_DIRS = frozenset(
    {Path("/bin"), Path("/sbin"), Path("/usr/bin"), Path("/usr/sbin")}
)


class ProviderRegistry:
    """Owns the ordered provider list; iteration order is registration order.

    Where two providers could claim the same binary, ADR-0015 resolves the
    tie by `$PATH` order, not registry order -- callers walk providers per
    `$PATH` entry, not the registry as a global priority list.
    """

    def __init__(self) -> None:
        self._providers: list[Provider] = []

    def register(self, provider: Provider) -> None:
        self._providers.append(provider)

    def __iter__(self) -> Iterator[Provider]:
        return iter(self._providers)

    def __len__(self) -> int:
        return len(self._providers)

    def candidates_for(self, bin_path: Path) -> Iterator[Provider]:
        """Yield providers worth probing for one PATH candidate.

        A provider may cheaply recognise its own documented install layout
        after the path is resolved.  Those direct routes are exhaustive for
        the built-in providers, so the remaining built-ins need not repeat
        their metadata checks.  A path with no direct route remains
        deliberately conservative: custom/manual locations get the original
        registry walk.  Standard system bin directories are the one explicit
        exception; none of the provider layouts can claim them unless it
        already supplied a direct route.
        """
        real_path = resolve_cached(bin_path)
        candidates: list[Provider] = []
        has_direct_route = False
        for provider in self:
            route = getattr(provider, "can_detect", None)
            if route is None:
                candidates.append(provider)
            elif route(real_path):
                candidates.append(provider)
                has_direct_route = True

        if has_direct_route:
            yield from candidates
            return
        if real_path.parent in _SYSTEM_BIN_DIRS:
            yield from candidates
            return
        yield from self


registry = ProviderRegistry()

registry.register(LocalLibProvider())
registry.register(UvProvider())
registry.register(MiseProvider())
registry.register(NpmProvider())
registry.register(PipxProvider())
registry.register(CargoProvider())
registry.register(GoProvider())
registry.register(HomebrewProvider())
