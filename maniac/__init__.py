from maniac.compiler import compile_to_man, install_manpage
from maniac.crawler import find_subcommands, format_help_block, get_help
from maniac.discovery import RepoSource, discover_repo
from maniac.docs import DocFile, extract_docs_from_dir, fetch_and_extract_docs
from maniac.extractor import extract_subcommands
from maniac.llm import run_llm_synthesis
from maniac.pipeline import PipelineResult, run_pipeline
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
    "format_help_block",
    "get_help",
    "install_manpage",
    "load_system_prompt",
    "run_llm_synthesis",
    "run_pipeline",
]
