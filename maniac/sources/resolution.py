"""Resolve PATH binaries through the ordered installer-provider registry."""

import os
from collections.abc import Callable
from pathlib import Path

from ..config import Config
from ..models import Installation, RepoSource
from . import loginpath
from .discovery import resolve_bin_path
from .providers.base import Provider
from .providers.cargo import CargoProvider
from .providers.go import GoProvider
from .providers.homebrew import HomebrewProvider
from .providers.local_lib import LocalLibProvider
from .providers.mise import MiseProvider
from .providers.npm import NpmProvider
from .providers.pipx import PipxProvider
from .providers.registry import ProviderRegistry
from .providers.uv import UvProvider

registry = ProviderRegistry()
registry.register(LocalLibProvider())
registry.register(UvProvider())
registry.register(MiseProvider())
registry.register(NpmProvider())
registry.register(PipxProvider())
registry.register(CargoProvider())
registry.register(GoProvider())
registry.register(HomebrewProvider())


def _find_provider(name: str) -> Provider | None:
    return next((provider for provider in registry if provider.name == name), None)


def _resolve_composed_source(parent: Installation, config: Config) -> RepoSource | None:
    provider = _find_provider(parent.provider)
    return provider.resolve_source(parent, config=config) if provider else None


MiseProvider.set_composed_source_resolver(_resolve_composed_source)


def find_installation(
    binary_name: str, bin_dir: str | Path | None = None
) -> tuple[Provider, Installation] | None:
    """Return the provider and installation record for a resolved binary."""
    bin_path = resolve_bin_path(binary_name, bin_dir)
    return _detect_via_registry(bin_path) if bin_path is not None else None


def discover_repo(
    binary_name: str, bin_dir: str | Path | None = None, *, config: Config
) -> RepoSource | None:
    """Resolve a binary's upstream source from its provider-owned installation."""
    found = find_installation(binary_name, bin_dir=bin_dir)
    if found is None:
        return None
    provider, inst = found
    return provider.resolve_source(inst, config=config)


def enumerate_installations(
    on_start: Callable[[int], None] | None = None,
    on_scan: Callable[[], None] | None = None,
) -> list[tuple[Provider, Installation]]:
    """Claim each first-PATH binary once, preserving PATH precedence."""
    seen: dict[str, Path] = {}
    for entry in loginpath.login_path_dirs():
        try:
            children = list(os.scandir(entry))
        except OSError:
            continue
        for child in children:
            if child.name in seen:
                continue
            try:
                if not child.is_file() or not os.access(child.path, os.X_OK):
                    continue
            except OSError:
                continue
            seen[child.name] = Path(child.path)

    if on_start is not None:
        on_start(len(seen))

    found: list[tuple[Provider, Installation]] = []
    for bin_path in seen.values():
        claim = _detect_via_registry(bin_path)
        if claim is not None:
            found.append(claim)
        if on_scan is not None:
            on_scan()
    return sorted(found, key=lambda item: item[1].binary)


def _detect_via_registry(bin_path: Path) -> tuple[Provider, Installation] | None:
    """Return the first registered provider that claims one PATH candidate."""
    for provider in registry.candidates_for(bin_path):
        inst = provider.detect(bin_path)
        if inst is not None:
            return provider, inst
    return None
