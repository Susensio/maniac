"""CLI help and upstream documentation source extraction package."""

from .crawler import find_subcommands, format_help_block, get_help
from .discovery import discover_repo
from .docs import (
    extract_docs_from_dir,
    fetch_and_extract_docs,
    format_docs_section,
)
from .extractor import extract_subcommands

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
