"""The `Provider` protocol every installer implements (ADR-0015), plus the
optional capabilities a caller may ask a provider for by type.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable

from ...config import Config
from ...models import Installation, RepoSource


class SourceResolver(Protocol):
    """Resolves any installation's upstream, whichever provider owns it.

    The registry implements this and passes itself to `resolve_source`, so a
    provider composing another installer's install (mise's backends) asks for
    that resolution through the resolver that started the current one, never
    through a peer import or process-global state.
    """

    def resolve_source(
        self, inst: Installation, *, config: Config
    ) -> RepoSource | None: ...


class Provider(Protocol):
    """Detects one installer's installations, resolves their upstream, lists local docs.

    No provider imports another; composition happens through
    `Installation.parent` and the registry that owns the provider list.
    """

    name: str

    def detect(self, bin_path: Path) -> Installation | None:
        """Pure-filesystem and cheap: claim `bin_path` or return None."""
        ...

    def resolve_source(
        self, inst: Installation, *, config: Config, sources: SourceResolver
    ) -> RepoSource | None:
        """May consult a registry; returns where `inst`'s documentation lives upstream.

        `sources` resolves an installation this provider only describes --
        a parent install belonging to another provider.
        """
        ...

    def local_docs(self, inst: Installation) -> list[Path]:
        """Pure-filesystem: documentation files already present under `inst.root`."""
        ...


@runtime_checkable
class RoutableProvider(Protocol):
    """Optional: recognises this provider's own install layout from a resolved path.

    A provider without it is routed nowhere and stays in the registry's
    conservative full walk (`ProviderRegistry.candidates_for`).
    """

    def can_detect(self, real_path: Path) -> bool: ...


@runtime_checkable
class DirectPageProvider(Protocol):
    """Optional: names a provider-owned manpage path to link instead of copying.

    Ownership stays with the provider: MANIAC records the target, links to it,
    and on uninstall removes only its own manpath link (ADR-0029/0030/0031).

    Freshness is the provider's too, because such a target may advance with a
    provider upgrade. A recorded target is therefore re-checked against the
    currently inspected installation rather than assumed still to describe it.
    """

    def direct_page_target(self, inst: Installation, page: Path) -> Path | None:
        """Provider-owned path to link for `page`, or None for MANIAC's durable copy."""
        ...

    def is_direct_page_target_current(
        self, inst: Installation, target: Path
    ) -> bool: ...
