"""The `Provider` protocol every installer implements (ADR-0015)."""

from pathlib import Path
from typing import Protocol

from ...models import Installation, RepoSource


class Provider(Protocol):
    """Detects one installer's installations, resolves their upstream, lists local docs.

    No provider imports another; composition happens through
    `Installation.parent` and the registry that owns the provider list.
    """

    name: str

    def detect(self, bin_path: Path) -> Installation | None:
        """Pure-filesystem and cheap: claim `bin_path` or return None."""
        ...

    def resolve_source(self, inst: Installation) -> RepoSource | None:
        """May consult a registry; returns where `inst`'s documentation lives upstream."""
        ...

    def local_docs(self, inst: Installation) -> list[Path]:
        """Pure-filesystem: documentation files already present under `inst.root`."""
        ...
