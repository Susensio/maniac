"""LLM synthesis execution."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
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
    work_base_dir: str | Path | None = None,
    timeout: int | None = None,
    clean_header: bool = True,
    config: Config | None = None,
) -> str:
    """Generate a response through the configured LLM backend."""
    cfg = config or Config()
    effective_timeout = timeout if timeout is not None else cfg.timeout_llm
    selected_model = cfg.resolve_model(model)

    if cfg.llm_backend == "litellm":
        raw_output = _run_litellm_synthesis(
            prompt,
            selected_model,
            effective_timeout,
            cfg,
            cfg.resolve_reasoning_effort(model),
        )
    elif cfg.llm_backend == "agy":
        raw_output = _run_agy_synthesis(
            prompt, tool_name, selected_model, effective_timeout, work_base_dir, cfg
        )
    else:
        raise GenerationError(
            f"Unsupported LLM backend '{cfg.llm_backend}'. Use 'litellm' or 'agy'."
        )

    return clean_manpage_markdown(raw_output, tool_name) if clean_header else raw_output


def _run_litellm_synthesis(
    prompt: str,
    model: str,
    timeout: int,
    cfg: Config,
    reasoning_effort: str | None,
) -> str:
    """Generate a response through LiteLLM with configured credentials."""
    request: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "timeout": timeout,
    }
    if cfg.llm_api_key:
        request["api_key"] = cfg.llm_api_key
    if reasoning_effort:
        request["reasoning_effort"] = reasoning_effort

    try:
        response = _complete_litellm(request)
    except _litellm_error_types() as e:
        logger.error("LLM API request failed", model=model, error=str(e))
        raise GenerationError(f"LLM API request failed: {e}") from e

    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise GenerationError("LLM API returned no text content.")
    return content.strip()


def _complete_litellm(request: dict[str, object]) -> _LiteLLMResponse:
    """Send a non-streaming LiteLLM completion request."""
    # LiteLLM otherwise fetches a remote pricing map during import.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
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


def _run_agy_synthesis(
    prompt: str,
    tool_name: str,
    model: str,
    timeout: int,
    work_base_dir: str | Path | None,
    cfg: Config,
) -> str:
    """Generate a response through the legacy agy CLI backend."""
    base_dir = (
        Path(work_base_dir).resolve()
        if work_base_dir is not None
        else cfg.work_base_dir
    )
    base_dir.mkdir(parents=True, exist_ok=True)

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"maniac_{tool_name}_", dir=str(base_dir)))
    logger.debug("Created temporary workspace for LLM", dir=str(tmp_dir))

    which_sandbox = shutil.which("sandbox")
    sandbox_bin = (
        Path(which_sandbox) if which_sandbox else (Path.home() / "bin" / "sandbox")
    )
    agy_bin = shutil.which("agy")

    executable = (
        [str(sandbox_bin), "agy"]
        if sandbox_bin.exists()
        else ([agy_bin] if agy_bin else None)
    )
    if not executable:
        logger.error("Neither sandbox nor agy found in PATH or ~/bin.")
        raise FileNotFoundError("Neither sandbox nor agy executable found.")

    effective_prompt = prompt
    prompt_bytes = effective_prompt.encode("utf-8")
    if len(prompt_bytes) > cfg.max_arg_limit:
        logger.warning(
            "Prompt exceeds argument limit; trimming context for single-turn synthesis",
            prompt_bytes=len(prompt_bytes),
        )
        cut_limit = max(0, cfg.max_arg_limit - 1000)
        trimmed_str = prompt_bytes[:cut_limit].decode("utf-8", errors="ignore")
        effective_prompt = trimmed_str + "\n\n=== [Context trimmed for synthesis] ==="

    cmd = [*executable, "--model", model, "-p", effective_prompt]

    try:
        logger.info(
            "Calling LLM synthesis",
            tool=tool_name,
            model=model,
        )
        res = subprocess.run(
            cmd,
            cwd=str(tmp_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if res.returncode != 0:
            logger.error(
                "LLM synthesis failed",
                returncode=res.returncode,
                stderr=res.stderr.strip(),
            )
            raise GenerationError(f"LLM command failed: {res.stderr.strip()}")

        return res.stdout.strip()
    except subprocess.TimeoutExpired as e:
        logger.error("LLM synthesis timed out", tool=tool_name)
        raise GenerationError(f"LLM synthesis timed out for '{tool_name}'") from e
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


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
