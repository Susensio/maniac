"""Manpage generation package (prompts, LLM execution, compiler)."""

from maniac.generation.compiler import compile_to_man, install_manpage
from maniac.generation.llm import clean_manpage_markdown, run_llm_synthesis
from maniac.generation.prompts import (
    DEFAULT_SYSTEM_PROMPT,
    build_synthesis_prompt,
    load_system_prompt,
)

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "build_synthesis_prompt",
    "clean_manpage_markdown",
    "compile_to_man",
    "install_manpage",
    "load_system_prompt",
    "run_llm_synthesis",
]
