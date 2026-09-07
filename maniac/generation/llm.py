"""LLM synthesis execution via LiteLLM."""

from typing import Protocol, cast

from ..config import Config
from ..exceptions import GenerationError
from ..logging import logger


class _LiteLLMMessage(Protocol):
    content: str | None


class _LiteLLMChoice(Protocol):
    message: _LiteLLMMessage


class _LiteLLMResponse(Protocol):
    choices: list[_LiteLLMChoice]


def run_llm_synthesis(
    prompt: str,
    tool_name: str,
    model: str | None = None,
    reasoning_effort: str | None = None,
    timeout: int | None = None,
    clean_header: bool = True,
    config: Config | None = None,
) -> str:
    """Generate a response through LiteLLM with configured credentials."""
    cfg = config or Config()
    effective_timeout = timeout if timeout is not None else cfg.timeout_llm
    selected_model = cfg.resolve_model(model)
    effort = (
        reasoning_effort
        if reasoning_effort is not None
        else cfg.resolve_reasoning_effort()
    )

    request: dict[str, object] = {
        "model": selected_model,
        "messages": [{"role": "user", "content": prompt}],
        "timeout": effective_timeout,
    }
    if cfg.llm_api_key:
        request["api_key"] = cfg.llm_api_key
    if effort and _supports_reasoning(selected_model):
        request["reasoning_effort"] = effort

    try:
        response = _complete_litellm(request)
    except _litellm_error_types() as e:
        logger.error("LLM API request failed", model=selected_model, error=str(e))
        raise GenerationError(f"LLM API request failed: {e}") from e

    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise GenerationError("LLM API returned no text content.")
    raw_output = content.strip()

    return clean_manpage_markdown(raw_output, tool_name) if clean_header else raw_output


def _supports_reasoning(model: str) -> bool:
    """Report whether LiteLLM sends `reasoning_effort` through for this model."""
    import litellm

    return bool(litellm.supports_reasoning(model=model))


def _complete_litellm(request: dict[str, object]) -> _LiteLLMResponse:
    """Send a non-streaming LiteLLM completion request."""
    from litellm import completion

    return cast(_LiteLLMResponse, completion(**request))


def _litellm_error_types() -> tuple[type[Exception], ...]:
    """Return provider exceptions LiteLLM can raise for a completion request."""
    from litellm.exceptions import (
        APIConnectionError,
        APIError,
        AuthenticationError,
        BadRequestError,
        BudgetExceededError,
        InternalServerError,
        RateLimitError,
        ServiceUnavailableError,
        Timeout,
    )

    return (
        APIConnectionError,
        APIError,
        AuthenticationError,
        BadRequestError,
        BudgetExceededError,
        InternalServerError,
        RateLimitError,
        ServiceUnavailableError,
        Timeout,
        ValueError,
    )


def clean_manpage_markdown(text: str, tool_name: str) -> str:
    """Unwrap code fences if present and ensure valid manpage metadata header."""
    cleaned = text.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    expected_header = f"% {tool_name.upper()}(1) | User Commands"
    if not cleaned.startswith("% "):
        cleaned = f"{expected_header}\n\n{cleaned}"

    return cleaned
