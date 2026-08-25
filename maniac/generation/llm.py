"""Direct LLM synthesis execution using the local sandbox environment."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from ..config import DEFAULT_MODEL_ALIASES, Config
from ..exceptions import GenerationError

MODEL_ALIASES = DEFAULT_MODEL_ALIASES


def run_llm_synthesis(
    prompt: str,
    tool_name: str,
    model: str | None = None,
    work_base_dir: str | Path = "data/tmp",
    timeout: int = 180,
    clean_header: bool = True,
    config: Config | None = None,
) -> str:
    """Execute direct LLM generation via ~/bin/sandbox agy -p."""
    cfg = config or Config()
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

    selected_model = cfg.resolve_model(model)

    effective_prompt = prompt
    if len(effective_prompt.encode("utf-8")) > cfg.max_arg_limit:
        logger.warning(
            "Prompt exceeds argument limit ({} bytes); trimming context for single-turn synthesis.",
            len(effective_prompt.encode("utf-8")),
        )
        effective_prompt = (
            effective_prompt[: cfg.max_arg_limit - 1000]
            + "\n\n=== [Context trimmed for synthesis] ==="
        )

    cmd = [*executable, "--model", selected_model, "-p", effective_prompt]

    try:
        logger.info(
            "Calling LLM synthesis for '{}' using model '{}'...",
            tool_name,
            selected_model,
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
            raise GenerationError(f"LLM command failed: {res.stderr.strip()}")

        raw_output = res.stdout.strip()
        if clean_header:
            return clean_manpage_markdown(raw_output, tool_name)
        return raw_output
    except subprocess.TimeoutExpired as e:
        logger.error("LLM synthesis timed out for '{}'", tool_name)
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
