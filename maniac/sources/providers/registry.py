"""Ordered registry of providers: registration plus iteration, nothing else."""

from collections.abc import Iterator

from .base import Provider


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


# The one registry the rest of the codebase composes providers through.
# Empty at Stage 1: no concrete provider exists yet to register into it.
registry = ProviderRegistry()
