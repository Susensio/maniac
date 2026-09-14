"""One resolved tool, shared by every install tier.

ADR-0016's tiers and tier-3 synthesis all need the same facts about one
binary: which provider claims it, where it is installed, at what version,
and which repository documents it. Resolving those once keeps ADR-0019's
version-match evidence inside a single flow, instead of tier 3 asking
`find_installation` and `discover_repo` again for answers tiers 1-2 already
had.
"""

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from ..config import Config
from ..models import Installation, RepoSource
from ..sources import documentation, resolution
from ..sources.providers.base import Provider
from ..sources.providers.registry import registry

__all__ = ["ResolvedTool", "resolve_tool"]


@dataclass(frozen=True)
class ResolvedTool:
    """What one resolution found for a binary, plus where its docs are cached.

    `provider` and `installation` are both None when no provider claims the
    binary (ADR-0015); neither is ever set without the other.
    """

    tool_name: str
    config: Config
    cache_dir: Path
    bin_dir: Path | None = None
    provider: Provider | None = None
    installation: Installation | None = None

    @property
    def executable(self) -> str:
        """Command that runs this binary, bin-dir-qualified when one was named."""
        if self.bin_dir is None:
            return self.tool_name
        return str(self.bin_dir / self.tool_name)

    @property
    def installed_version(self) -> str | None:
        """Version the provider reports, or None when nothing claims the binary."""
        return self.installation.version if self.installation is not None else None

    @cached_property
    def documentation_source(self) -> RepoSource | None:
        """Canonical documentation repository, or None when none resolves.

        Resolved on first use rather than at construction: tier 1 answering
        from the install root must not pay provider source resolution.
        Cached, so tier 2 and tier 3 share one answer and one cost.
        """
        if self.provider is None or self.installation is None:
            return None
        source = registry.resolve_source(
            self.installation, config=self.config, provider=self.provider
        )
        if source is None:
            return None
        return documentation.documentation_source(
            source, self.config.documentation_repository_overrides
        )


def resolve_tool(
    tool_name: str,
    *,
    config: Config,
    cache_dir: str | Path | None = None,
    bin_dir: str | Path | None = None,
) -> ResolvedTool:
    """Resolve a binary's installation once, for every tier that follows."""
    found = resolution.find_installation(tool_name, bin_dir=bin_dir)
    provider, installation = found if found is not None else (None, None)
    return ResolvedTool(
        tool_name=tool_name,
        config=config,
        cache_dir=Path(cache_dir) if cache_dir is not None else config.cache_dir,
        bin_dir=Path(bin_dir) if bin_dir is not None else None,
        provider=provider,
        installation=installation,
    )
