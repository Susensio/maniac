"""The row a lookup stands for, shared by `list`, `scan` and `why`."""

from ..sources.lookup import resolution
from .models import Candidate, ToolError


def named_candidate(tool: str) -> Candidate:
    """Resolve one tool by name; a tool nothing claims still gets a candidate.

    No bare-name repository fallback when nothing is found (ADR-0015). A tool whose
    metadata is unreadable, or refused as not globally selected, carries
    why in `Candidate.error`.
    """
    return candidate_from(resolution.locate(tool))


def candidate_from(located: resolution.Located) -> Candidate:
    """The row a lookup stands for: its claim, or why it was refused."""
    if located.error is not None:
        return Candidate(
            tool=located.tool,
            provider=None,
            installation=None,
            error=ToolError(located.error.path, located.error.reason),
        )
    return Candidate(
        tool=located.tool,
        provider=located.provider,
        installation=located.installation,
        binary=located.binary,
    )
