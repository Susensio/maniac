"""Maniac: Scrape CLI help, extract repository docs, and synthesize elite Unix manpages."""

from maniac.config import Config
from maniac.eval import DeterministicCheck, LLMJudge, evaluate_manpage
from maniac.exceptions import (
    CrawlerError,
    DiscoveryError,
    GenerationError,
    ManiacError,
)
from maniac.generation.compiler import compile_to_man, install_manpage
from maniac.generation.llm import clean_manpage_markdown, run_llm_synthesis
from maniac.generation.prompts import (
    DEFAULT_SYSTEM_PROMPT,
    build_synthesis_prompt,
    load_system_prompt,
)
from maniac.models import DocFile, EvaluationResult, PipelineResult, RepoSource
from maniac.orchestration.pipeline import run_pipeline
from maniac.sources.crawler import find_subcommands, format_help_block, get_help
from maniac.sources.discovery import discover_repo
from maniac.sources.docs import (
    extract_docs_from_dir,
    fetch_and_extract_docs,
    format_docs_section,
)
from maniac.sources.extractor import extract_subcommands

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "Config",
    "CrawlerError",
    "DeterministicCheck",
    "DiscoveryError",
    "DocFile",
    "EvaluationResult",
    "GenerationError",
    "LLMJudge",
    "ManiacError",
    "PipelineResult",
    "RepoSource",
    "build_synthesis_prompt",
    "clean_manpage_markdown",
    "compile_to_man",
    "discover_repo",
    "evaluate_manpage",
    "extract_docs_from_dir",
    "extract_subcommands",
    "fetch_and_extract_docs",
    "find_subcommands",
    "format_docs_section",
    "format_help_block",
    "get_help",
    "install_manpage",
    "load_system_prompt",
    "run_llm_synthesis",
    "run_pipeline",
]
