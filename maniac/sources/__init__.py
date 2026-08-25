"""Documentation and CLI help source extraction package."""

from maniac.sources.crawler import find_subcommands, format_help_block, get_help
from maniac.sources.discovery import discover_repo
from maniac.sources.docs import (
    extract_docs_from_dir,
    fetch_and_extract_docs,
    format_docs_section,
)
from maniac.sources.extractor import extract_subcommands

__all__ = [
    "discover_repo",
    "extract_docs_from_dir",
    "extract_subcommands",
    "fetch_and_extract_docs",
    "find_subcommands",
    "format_docs_section",
    "format_help_block",
    "get_help",
]
