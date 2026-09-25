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
