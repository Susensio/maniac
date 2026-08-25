"""Manpage generation package (prompts, LLM synthesis, Pandoc compiler)."""

from .compiler import compile_to_man, install_manpage
from .llm import clean_manpage_markdown, run_llm_synthesis
from .prompts import (
    build_synthesis_prompt,
    get_default_system_prompt,
    load_system_prompt,
)

__all__ = [
    "build_synthesis_prompt",
    "clean_manpage_markdown",
    "compile_to_man",
    "get_default_system_prompt",
    "install_manpage",
    "load_system_prompt",
    "run_llm_synthesis",
]
