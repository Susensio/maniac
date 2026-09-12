"""Documentation-canonical repository identities."""

from dataclasses import replace

from ..models import RepoSource

# Distribution repositories can be release-only mirrors.  These exact, reviewed
# identities keep documentation discovery tied to the project repository without
# guessing from a repository or binary name.
_DOCUMENTATION_REPOSITORY_OVERRIDES = {
    "tmux/tmux-builds": "tmux/tmux",
}


def documentation_source(source: RepoSource) -> RepoSource:
    """A copy of ``source`` redirected only when its docs repo is explicitly known."""
    target = _DOCUMENTATION_REPOSITORY_OVERRIDES.get(source.target)
    return replace(source, target=target) if target is not None else source
