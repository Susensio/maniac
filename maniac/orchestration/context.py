"""One resolved tool, shared by every install tier.

ADR-0016's tiers and tier-3 synthesis all need the same facts about one
binary: which provider claims it, where it is installed, at what version,
and which repository documents it. Resolving those once keeps ADR-0019's
version-match evidence inside a single flow, instead of tier 3 resolving
the installation and its repository again for answers tiers 1-2 already
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
    bin_path: Path | None = None
    """The binary that runs: the claimed installation's file, else
    `resolution.binary_path` (ADR-0062's login `$PATH` or `bin_dir`, a
    Mise shim replaced by its target, ADR-0063)."""
    pinned: bool = False
    """The page records which binary it documents: a copy maniac would not
    have chosen by itself -- `install --force` past a refusal (CONTRACT.md
    rules 1 and 2) -- so `list` and `update` keep to that copy."""

    @property
    def documented_binary(self) -> Path | None:
        """The copy a page records documenting; only a pinned one is recorded."""
        return self.bin_path if self.pinned else None

    @property
    def command(self) -> str:
        """How the crawled help names this binary, bin-dir-qualified when one was named."""
        if self.bin_dir is None:
            return self.tool_name
        return str(self.bin_dir / self.tool_name)

    @property
    def executable(self) -> str:
        """What actually runs: the resolved path whenever there is one.

        Never the bare name when a path was resolved -- `--help` and
        `--version` run through `subprocess`, which searches the `$PATH`
        maniac inherited, so inside an activated venv a bare `ruff` would
        crawl the venv's copy while resolution documented the global one.
        """
        if self.bin_path is not None:
            return str(self.bin_path)
        return self.command

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
    here: bool = False,
) -> ResolvedTool:
    """Resolve a binary's installation once, for every tier that follows.

    Raises the lookup's refusal (`NotGloballySelected`, `ShimRunsNothing`,
    `MalformedToolMetadata`) for a caller that has nothing better to say.
    """
    return tool_from(
        resolution.locate(tool_name, bin_dir, accept_project=here),
        config=config,
        cache_dir=cache_dir,
        bin_dir=bin_dir,
        pinned=here,
    )


def tool_from(
    located: resolution.Located,
    *,
    config: Config,
    cache_dir: str | Path | None = None,
    bin_dir: str | Path | None = None,
    pinned: bool = False,
) -> ResolvedTool:
    """The resolved tool a lookup found; raises its refusal if it found none."""
    if located.error is not None:
        raise located.error
    return ResolvedTool(
        # What runs: the claimed file, else the unclaimed binary -- for a
        # Mise shim either way its `$HOME` target, never the shim (ADR-0063).
        bin_path=located.binary,
        tool_name=located.tool,
        config=config,
        cache_dir=Path(cache_dir) if cache_dir is not None else config.cache_dir,
        bin_dir=Path(bin_dir) if bin_dir is not None else None,
        provider=located.provider,
        installation=located.installation,
        pinned=pinned,
    )
