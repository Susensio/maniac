"""Quality evaluation and LLM-as-a-Judge package."""

from .judge import (
    DeterministicCheck,
    LLMJudge,
    build_evaluation_prompt,
    evaluate_manpage,
    get_default_eval_prompt,
    parse_evaluation_json,
)

__all__ = [
    "DeterministicCheck",
    "LLMJudge",
    "build_evaluation_prompt",
    "evaluate_manpage",
    "get_default_eval_prompt",
    "parse_evaluation_json",
]
