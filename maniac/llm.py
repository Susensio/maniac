import shutil
import subprocess
import tempfile
from pathlib import Path

from loguru import logger


def run_llm_synthesis(
    prompt: str,
    tool_name: str,
    work_base_dir: str | Path = "data/tmp",
    timeout: int = 300,
) -> str:
    """Execute LLM generation via ~/bin/sandbox agy in an isolated directory using a prompt file."""
    base_dir = Path(work_base_dir).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"maniac_{tool_name}_", dir=str(base_dir)))
    logger.debug("Created temporary workspace for LLM at {}", tmp_dir)

    # Write prompt to workspace file to prevent exceeding OS argument length limits
    prompt_file = tmp_dir / "prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")

    sandbox_bin = Path.home() / "bin" / "sandbox"
    agy_bin = shutil.which("agy")

    driver_instruction = (
        f"Read the prompt and context in prompt.md in the current directory, "
        f"follow all formatting rules and guidelines strictly, "
        f"and output ONLY the raw synthesized Markdown manpage for '{tool_name}'."
    )

    if sandbox_bin.exists():
        cmd = [
            str(sandbox_bin),
            "agy",
            "--dangerously-skip-permissions",
            "-p",
            driver_instruction,
        ]
    elif agy_bin:
        cmd = [agy_bin, "--dangerously-skip-permissions", "-p", driver_instruction]
    else:
        logger.error("Neither ~/bin/sandbox nor agy found in PATH.")
        raise FileNotFoundError("Neither ~/bin/sandbox nor agy executable found.")

    try:
        logger.info("Calling LLM synthesis for '{}'...", tool_name)
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
        # Drop first line if it starts with ```
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        # Drop last line if it is ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    # Ensure % TOOL(1) header exists on the very first line
    expected_header = f"% {tool_name.upper()}(1) | User Commands"
    if not cleaned.startswith("% "):
        cleaned = f"{expected_header}\n\n{cleaned}"

    return cleaned
