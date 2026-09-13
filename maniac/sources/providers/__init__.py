"""Installer providers and the registry they share (ADR-0015)."""

import sys

from .base import Provider
from .registry import ProviderRegistry, registry

# A direct legacy-registry import enters this package before loading the
# submodule. Bootstrap resolution here so that import keeps its populated
# registry contract. When resolution itself imports provider types, its
# partially initialized module is present and registration continues there.
if "maniac.sources.resolution" not in sys.modules:
    from .. import resolution as _resolution

    assert registry is _resolution.registry

__all__ = ["Provider", "ProviderRegistry", "registry"]
