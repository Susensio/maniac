import os
import subprocess

from loguru import logger
from maniac.extractor import extract_subcommands


def get_help(cmd: list[str]) -> str:
    full_cmd = [*cmd, "--help"]
    cmd_str = " ".join(full_cmd)
    logger.info("Executing: {}", cmd_str)
    env = os.environ | {"PAGER": "cat", "NO_COLOR": "1", "TERM": "dumb"}
    try:
        res = subprocess.run(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5,
            errors="replace",
            env=env,
        )
        if res.returncode != 0:
            logger.warning("Command '{}' exited with code {}", cmd_str, res.returncode)
        return (res.stdout or "").strip()
    except subprocess.TimeoutExpired:
        logger.error("Timed out running '{}'", cmd_str)
        return "Error: Command timed out."
    except (FileNotFoundError, OSError) as e:
        logger.error("Failed to run '{}': {}", cmd_str, e)
        return f"Error: {e}"


def find_subcommands(
    cmd: str | list[str],
    results: dict[str, str] | None = None,
    visited_outputs: set[str] | None = None,
) -> dict[str, str]:
    if isinstance(cmd, str):
        cmd = cmd.split()
    results = {} if results is None else results
    visited_outputs = set() if visited_outputs is None else visited_outputs

    key = f"> {' '.join(cmd)} --help"
    if key in results:
        return results

    help_text = get_help(cmd)
    results[key] = help_text

    # Stop recursion if command errored or output was already seen in tree/ancestors
    if help_text.startswith("Error:") or help_text in visited_outputs:
        return results

    visited_outputs.add(help_text)

    for sub in extract_subcommands(help_text, cmd_name=cmd[-1]):
        find_subcommands([*cmd, sub], results, visited_outputs=visited_outputs)

    return results
