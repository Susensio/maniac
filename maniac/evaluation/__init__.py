"""Quality evaluation and LLM-as-a-Judge package."""

from ..models import CoverageStats
from .judge import (
    REQUIRED_SECTIONS,
    build_evaluation_prompt,
    check_metadata_header,
    check_pandoc_compilation,
    check_standard_sections,
    compute_coverage,
    evaluate_manpage,
    get_default_eval_prompt,
    parse_evaluation_json,
    run_deterministic_checks,
    run_llm_judge,
)

__all__ = [
    "REQUIRED_SECTIONS",
    "CoverageStats",
    "build_evaluation_prompt",
    "check_metadata_header",
    "check_pandoc_compilation",
    "check_standard_sections",
    "compute_coverage",
    "evaluate_manpage",
    "get_default_eval_prompt",
    "parse_evaluation_json",
    "run_deterministic_checks",
    "run_llm_judge",
]
