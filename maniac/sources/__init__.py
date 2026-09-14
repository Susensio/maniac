"""CLI help and upstream documentation source extraction package."""

from .crawler import (
    extract_subcommands,
    find_subcommands,
    format_help_block,
    get_help,
)
from .resolution import discover_repo

__all__ = [
    "discover_repo",
    "extract_subcommands",
    "find_subcommands",
    "format_help_block",
    "get_help",
]
