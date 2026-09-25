"""Exception hierarchy for the maniac package."""

from pathlib import Path


class ManiacError(Exception):
    """Base exception for all maniac errors."""


class CrawlerError(ManiacError):
    """Raised when crawling CLI help fails."""


class GenerationError(ManiacError):
    """Raised when generating manpages via LLM or compiler fails."""


class MalformedToolMetadata(ManiacError):
    """A tool's own metadata file is present but cannot be read (ADR-0060).

    Raised at the point a malformed file is read, never swallowed into a
    guess or a silent skip. A missing file is a distinct, ordinary case and
    never raises this.
    """

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


class ProjectScopedInstall(ManiacError):
    """A Mise installation exists but is active only via a project's own
    config, not one of Mise's globally selected tools (ADR-0061).

    Refused outright rather than treated as unclaimed -- an unclaimed
    binary falls to tier-3 synthesis, which would document this project's
    version as if it were the machine's global one.
    """

    def __init__(self, tool: str, root: Path) -> None:
        self.tool = tool
        self.path = root
        self.reason = (
            "active only via a project config, not a globally selected mise tool"
        )
        super().__init__(f"{tool}: {self.reason} ({root})")
