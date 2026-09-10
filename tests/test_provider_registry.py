"""Tests for `ProviderRegistry` (ADR-0015): registration plus ordered iteration."""

from pathlib import Path

from maniac.config import Config
from maniac.models import Installation, RepoSource
from maniac.sources.providers import ProviderRegistry, registry


class _FakeProvider:
    """Minimal `Provider` stand-in; Stage 1 ships no concrete provider to test against."""

    def __init__(self, name: str) -> None:
        self.name = name

    def detect(self, bin_path: Path) -> Installation | None:
        return None

    def resolve_source(
        self, inst: Installation, *, config: Config
    ) -> RepoSource | None:
        return None

    def local_docs(self, inst: Installation) -> list[Path]:
        return []


def test_the_module_registry_holds_the_registered_providers() -> None:
    """`local_lib`, `uv`, `mise` register first, mirroring the prior check order;
    Stage 4 appends npm, pipx, cargo, go, Homebrew -- registration order settles
    nothing between providers (ADR-0015: `$PATH` order breaks ties), only the
    diff staying a pure append.
    """
    assert [provider.name for provider in registry] == [
        "local_lib",
        "uv",
        "mise",
        "npm",
        "pipx",
        "cargo",
        "go",
        "homebrew",
    ]


def test_register_appends_and_iteration_preserves_order() -> None:
    reg = ProviderRegistry()
    first, second = _FakeProvider("mise"), _FakeProvider("uv")

    reg.register(first)
    reg.register(second)

    assert list(reg) == [first, second]
    assert len(reg) == 2


def test_fresh_registry_starts_empty() -> None:
    assert list(ProviderRegistry()) == []
