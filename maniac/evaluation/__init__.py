"""Quality evaluation and LLM-as-a-Judge package."""

from ..models import ComparisonResult, CoverageStats
from .judge import (
    REQUIRED_SECTIONS,
    build_comparison_prompt,
    build_evaluation_prompt,
    check_metadata_header,
    check_pandoc_compilation,
    check_standard_sections,
    compare_manpages,
    compute_coverage,
    evaluate_manpage,
    get_default_compare_prompt,
    get_default_eval_prompt,
    parse_comparison_json,
    parse_evaluation_json,
    run_comparison_judge,
    run_deterministic_checks,
    run_llm_judge,
)

__all__ = [
    "REQUIRED_SECTIONS",
    "ComparisonResult",
    "CoverageStats",
    "build_comparison_prompt",
    "build_evaluation_prompt",
    "check_metadata_header",
    "check_pandoc_compilation",
    "check_standard_sections",
    "compare_manpages",
    "compute_coverage",
    "evaluate_manpage",
    "get_default_compare_prompt",
    "get_default_eval_prompt",
    "parse_comparison_json",
    "parse_evaluation_json",
    "run_comparison_judge",
    "run_deterministic_checks",
    "run_llm_judge",
]
