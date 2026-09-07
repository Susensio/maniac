"""Installer providers: one module per installer, none calling another (ADR-0015).

Empty of concrete providers until Stage 2 of `docs/ROADMAP.md` ports mise,
uv tools and `~/.local/lib` behind `Provider`.
"""

from .base import Provider
from .registry import ProviderRegistry, registry

__all__ = ["Provider", "ProviderRegistry", "registry"]
