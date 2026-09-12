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


class _RoutedFakeProvider(_FakeProvider):
    """Records the cheap route check separately from an expensive detection."""

    def __init__(self, name: str, *, route: bool, claims: bool = False) -> None:
        super().__init__(name)
        self.route = route
        self.claims = claims
        self.route_paths: list[Path] = []
        self.detect_paths: list[Path] = []

    def can_detect(self, real_path: Path) -> bool:
        self.route_paths.append(real_path)
        return self.route

    def detect(self, bin_path: Path) -> Installation | None:
        self.detect_paths.append(bin_path)
        if not self.claims:
            return None
        return Installation(
            binary=bin_path.name,
            bin_path=bin_path,
            real_path=bin_path.resolve(),
            provider=self.name,
            package=bin_path.name,
            version=None,
            root=bin_path.parent,
        )


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


def test_direct_route_probes_only_matching_providers_in_registry_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tool"
    path.touch()
    first = _RoutedFakeProvider("first", route=True)
    skipped = _RoutedFakeProvider("skipped", route=False)
    second = _RoutedFakeProvider("second", route=True)
    reg = ProviderRegistry()
    for provider in (first, skipped, second):
        reg.register(provider)

    providers = list(reg.candidates_for(path))
    for provider in providers:
        provider.detect(path)

    assert providers == [first, second]
    assert first.detect_paths == [path]
    assert skipped.detect_paths == []
    assert second.detect_paths == [path]


def test_direct_route_keeps_the_order_of_an_unroutable_extension(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tool"
    path.touch()
    extension = _FakeProvider("extension")
    provider = _RoutedFakeProvider("managed", route=True)
    reg = ProviderRegistry()
    reg.register(extension)
    reg.register(provider)

    assert list(reg.candidates_for(path)) == [extension, provider]


def test_unknown_manual_path_falls_back_to_the_complete_registry_in_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manual" / "tool"
    path.parent.mkdir()
    path.touch()
    first = _RoutedFakeProvider("first", route=False)
    second = _RoutedFakeProvider("second", route=False, claims=True)
    reg = ProviderRegistry()
    reg.register(first)
    reg.register(second)

    for provider in reg.candidates_for(path):
        if provider.detect(path) is not None:
            break

    assert first.detect_paths == [path]
    assert second.detect_paths == [path]


def test_system_binary_without_a_direct_route_skips_provider_detection() -> None:
    path = Path("/usr/bin/env")
    assert path.is_file()
    provider = _RoutedFakeProvider("unrelated", route=False, claims=True)
    reg = ProviderRegistry()
    reg.register(provider)

    candidates = list(reg.candidates_for(path))
    for candidate in candidates:
        candidate.detect(path)

    assert candidates == []
    assert provider.detect_paths == []


def test_symlink_is_routed_by_its_resolved_target_but_detected_as_path_entry(
    tmp_path: Path,
) -> None:
    target = tmp_path / "managed" / "tool"
    target.parent.mkdir()
    target.touch()
    entry = tmp_path / "bin" / "tool"
    entry.parent.mkdir()
    entry.symlink_to(target)
    provider = _RoutedFakeProvider("managed", route=True, claims=True)
    reg = ProviderRegistry()
    reg.register(provider)

    detected = None
    for candidate in reg.candidates_for(entry):
        detected = candidate.detect(entry)
        if detected is not None:
            break

    assert provider.route_paths == [target]
    assert provider.detect_paths == [entry]
    assert detected is not None
    assert detected.binary == "tool"
