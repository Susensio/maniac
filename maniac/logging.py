"""Structured logging configuration for Maniac."""

import logging
from typing import Any

import structlog


def setup_logging(verbose: bool = False) -> None:
    """Configure structlog filtering and console formatting."""
    min_level = logging.DEBUG if verbose else logging.WARNING

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
        structlog.dev.ConsoleRenderer(colors=True),
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(min_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


logger = structlog.get_logger()
