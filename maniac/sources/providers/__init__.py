"""Installer providers and the registry they share (ADR-0015)."""

from .base import Provider
from .registry import ProviderRegistry, registry

__all__ = ["Provider", "ProviderRegistry", "registry"]
