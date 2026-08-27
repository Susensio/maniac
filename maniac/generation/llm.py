"""Direct LLM synthesis execution using the local sandbox environment."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from ..config import Config
from ..exceptions import GenerationError
from ..logging import logger


def run_llm_synthesis(
    prompt: str,
    tool_name: str,
    model: str | None = None,
    work_base_dir: str | Path | None = None,
    timeout: int | None = None,
    clean_header: bool = True,
    config: Config | None = None,
) -> str:
    """Execute direct LLM generation via sandbox/agy CLI."""
    cfg = config or Config()
    effective_timeout = timeout if timeout is not None else cfg.timeout_llm
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

    selected_model = cfg.resolve_model(model)

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

    cmd = [*executable, "--model", selected_model, "-p", effective_prompt]

    try:
        logger.info(
            "Calling LLM synthesis",
            tool=tool_name,
            model=selected_model,
        )
        res = subprocess.run(
            cmd,
            cwd=str(tmp_dir),
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            check=False,
        )
        if res.returncode != 0:
            logger.error(
                "LLM synthesis failed",
                returncode=res.returncode,
                stderr=res.stderr.strip(),
            )
            raise GenerationError(f"LLM command failed: {res.stderr.strip()}")

        raw_output = res.stdout.strip()
        if clean_header:
            return clean_manpage_markdown(raw_output, tool_name)
        return raw_output
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
