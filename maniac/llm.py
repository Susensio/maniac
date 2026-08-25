import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

# Linux kernel MAX_ARG_STRLEN is 131,072 bytes; keep safety margin
MAX_ARG_LIMIT = int(os.environ.get("MANIAC_MAX_ARG_LIMIT", "115000"))

# Friendly model aliases mapping to agy runtime models
MODEL_ALIASES = {
    "flash": "Gemini 3.7 Flash (High)",
    "flash-high": "Gemini 3.7 Flash (High)",
    "flash-medium": "Gemini 3.7 Flash (Medium)",
    "flash-low": "Gemini 3.7 Flash (Low)",
    "pro": "Gemini 3.1 Pro (High)",
    "pro-low": "Gemini 3.1 Pro (Low)",
    "sonnet": "Claude Sonnet 4.6 (Thinking)",
    "opus": "Claude Opus 4.6 (Thinking)",
}

DEFAULT_MODEL = os.environ.get("MANIAC_MODEL", "flash")


def run_llm_synthesis(
    prompt: str,
    tool_name: str,
    model: str | None = None,
    work_base_dir: str | Path = "data/tmp",
    timeout: int = 180,
) -> str:
    """Execute direct LLM generation via ~/bin/sandbox agy -p."""
    base_dir = Path(work_base_dir).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"maniac_{tool_name}_", dir=str(base_dir)))
    logger.debug("Created temporary workspace for LLM at {}", tmp_dir)

    sandbox_bin = Path.home() / "bin" / "sandbox"
    agy_bin = shutil.which("agy")

    executable = (
        [str(sandbox_bin), "agy"]
        if sandbox_bin.exists()
        else ([agy_bin] if agy_bin else None)
    )
    if not executable:
        logger.error("Neither ~/bin/sandbox nor agy found in PATH.")
        raise FileNotFoundError("Neither ~/bin/sandbox nor agy executable found.")

    raw_model = (model or DEFAULT_MODEL).strip()
    selected_model = MODEL_ALIASES.get(raw_model.lower(), raw_model)

    # Guard prompt string to fit cleanly within OS argument bounds
    effective_prompt = prompt
    if len(effective_prompt.encode("utf-8")) > MAX_ARG_LIMIT:
        logger.warning(
            "Prompt exceeds argument limit ({} bytes); trimming context for single-turn synthesis.",
            len(effective_prompt.encode("utf-8")),
        )
        effective_prompt = (
            effective_prompt[: MAX_ARG_LIMIT - 1000]
            + "\n\n=== [Context trimmed for synthesis] ==="
        )

    cmd = [*executable, "--model", selected_model, "-p", effective_prompt]

    try:
        logger.info(
            "Calling LLM synthesis for '{}' using model '{}' (alias: '{}')...",
            tool_name,
            selected_model,
            raw_model,
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
                "LLM synthesis failed with code {}: {}", res.returncode, res.stderr
            )
            raise RuntimeError(f"LLM command failed: {res.stderr.strip()}")

        raw_output = res.stdout.strip()
        return clean_manpage_markdown(raw_output, tool_name)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def clean_manpage_markdown(text: str, tool_name: str) -> str:
    """Unwrap code fences if present and ensure valid manpage metadata header."""
    cleaned = text.strip()

    # Unwrap triple backticks if output was wrapped
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    # Ensure % TOOL(1) header exists on the very first line
    expected_header = f"% {tool_name.upper()}(1) | User Commands"
    if not cleaned.startswith("% "):
        cleaned = f"{expected_header}\n\n{cleaned}"

    return cleaned
