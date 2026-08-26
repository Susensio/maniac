"""Exception hierarchy for the maniac package."""


class ManiacError(Exception):
    """Base exception for all maniac errors."""


class CrawlerError(ManiacError):
    """Raised when crawling CLI help fails."""


class GenerationError(ManiacError):
    """Raised when generating manpages via LLM or compiler fails."""
