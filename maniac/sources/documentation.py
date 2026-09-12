"""Documentation-canonical repository identities."""

from collections.abc import Mapping
from dataclasses import replace

from ..models import RepoSource


def documentation_source(
    source: RepoSource, documentation_repositories: Mapping[str, str]
) -> RepoSource:
    """Return ``source`` redirected only by an explicit documentation mapping."""
    target = documentation_repositories.get(source.target)
    return replace(source, target=target) if target is not None else source
