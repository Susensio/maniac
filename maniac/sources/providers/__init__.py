"""Installer providers and their registry types (ADR-0015)."""

from collections.abc import Iterator
from pathlib import Path

from .base import Provider
from .registry import ProviderRegistry

__all__ = ["Provider", "ProviderRegistry", "registry"]


class _RegistryProxy:
    """Compatibility access to the registry now owned by source resolution."""

    @staticmethod
    def _delegate() -> ProviderRegistry:
        from ..resolution import registry

        return registry

    def register(self, provider: Provider) -> None:
        self._delegate().register(provider)

    def __iter__(self) -> Iterator[Provider]:
        return iter(self._delegate())

    def __len__(self) -> int:
        return len(self._delegate())

    def candidates_for(self, bin_path: Path) -> Iterator[Provider]:
        return self._delegate().candidates_for(bin_path)


registry = _RegistryProxy()
