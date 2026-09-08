"""Tests for `ProviderRegistry` (ADR-0015): registration plus ordered iteration."""

from pathlib import Path

from maniac.models import Installation, RepoSource
from maniac.sources.providers import ProviderRegistry, registry


class _FakeProvider:
    """Minimal `Provider` stand-in; Stage 1 ships no concrete provider to test against."""

    def __init__(self, name: str) -> None:
        self.name = name

    def detect(self, bin_path: Path) -> Installation | None:
        return None

    def resolve_source(self, inst: Installation) -> RepoSource | None:
        return None

    def local_docs(self, inst: Installation) -> list[Path]:
        return []


def test_the_module_registry_holds_the_registered_providers() -> None:
    """`local_lib`, `uv`, `mise` register in that order, mirroring the prior check order."""
    assert [provider.name for provider in registry] == ["local_lib", "uv", "mise"]


def test_register_appends_and_iteration_preserves_order() -> None:
    reg = ProviderRegistry()
    first, second = _FakeProvider("mise"), _FakeProvider("uv")

    reg.register(first)
    reg.register(second)

    assert list(reg) == [first, second]
    assert len(reg) == 2


def test_fresh_registry_starts_empty() -> None:
    assert list(ProviderRegistry()) == []
