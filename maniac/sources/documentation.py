"""Documentation-canonical repository identities."""

from collections.abc import Mapping

from ..models import RemoteRepoSource, RepoSource


def documentation_source(
    source: RepoSource, documentation_repositories: Mapping[str, str]
) -> RepoSource:
    """Return ``source`` redirected only by an explicit documentation mapping."""
    target = documentation_repositories.get(source.identity)
    return (
        RemoteRepoSource.from_identifier(source.name, target)
        if target is not None
        else source
    )
