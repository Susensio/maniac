from maniac.compiler import compile_to_man, install_manpage
from maniac.crawler import find_subcommands, format_help_block, get_help
from maniac.discovery import discover_repo
from maniac.docs import (
    extract_docs_from_dir,
    fetch_and_extract_docs,
    format_docs_section,
)
from maniac.extractor import extract_subcommands
from maniac.llm import run_llm_synthesis
from maniac.models import DocFile, PipelineResult, RepoSource
from maniac.pipeline import run_pipeline
from maniac.prompts import build_synthesis_prompt, load_system_prompt

__version__ = "0.1.0"

__all__ = [
    "DocFile",
    "PipelineResult",
    "RepoSource",
    "build_synthesis_prompt",
    "compile_to_man",
    "discover_repo",
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
