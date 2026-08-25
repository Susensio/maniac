"""CLI help crawling and subcommand discovery."""

import os
import re
import subprocess

from loguru import logger

from ..exceptions import CrawlerError


def extract_subcommands(text: str, cmd_name: str | None = None) -> list[str]:
    """Extract subcommand names from structured CLI help text."""
    subcommands: list[str] = []
    in_commands = False
    for line in text.splitlines():
        if not line.startswith("   ") and (
            line.rstrip().endswith(":") or line.strip().isupper()
        ):
            in_commands = "command" in line.lower() or "action" in line.lower()
            continue

        indent = len(line) - len(line.lstrip())
        if in_commands and 0 < indent <= 4:
            parts = re.split(r"\s{2,}", line.strip(), maxsplit=1)
            tokens = parts[0].split()
            if tokens:
                idx = (
                    1 if (cmd_name and tokens[0] == cmd_name and len(tokens) > 1) else 0
                )
                cmd_token = tokens[idx].rstrip(":,")
                if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", cmd_token):
                    subcommands.append(cmd_token)
    return subcommands


def get_help(cmd: list[str], timeout: int = 5) -> str:
    """Execute command with --help and capture standard output."""
    full_cmd = [*cmd, "--help"]
    cmd_str = " ".join(full_cmd)
    logger.debug("Executing: {}", cmd_str)
    env = os.environ | {
        "PAGER": "cat",
        "BAT_PAGER": "",
        "GIT_PAGER": "",
        "SYSTEMD_PAGER": "cat",
        "NO_COLOR": "1",
        "TERM": "dumb",
    }
    try:
        res = subprocess.run(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            errors="replace",
            env=env,
            check=False,
        )
        if res.returncode != 0:
            logger.debug("Command '{}' exited with code {}", cmd_str, res.returncode)
        return (res.stdout or "").strip()
    except subprocess.TimeoutExpired as e:
        logger.warning("Timed out running '{}'", cmd_str)
        raise CrawlerError(f"Command timed out: '{cmd_str}'") from e
    except (FileNotFoundError, OSError) as e:
        logger.warning("Failed to run '{}': {}", cmd_str, e)
        raise CrawlerError(f"Failed to run '{cmd_str}': {e}") from e


def find_subcommands(
    cmd: str | list[str],
    results: dict[str, str] | None = None,
    visited_outputs: set[str] | None = None,
) -> dict[str, str]:
    """Recursively discover and extract help text for commands and subcommands."""
    if isinstance(cmd, str):
        cmd = cmd.split()
    results = {} if results is None else results
    visited_outputs = set() if visited_outputs is None else visited_outputs

    key = f"> {' '.join(cmd)} --help"
    if key in results:
        return results

    try:
        help_text = get_help(cmd)
    except CrawlerError:
        if not results:
            raise
        return results

    results[key] = help_text

    # Stop recursion if command errored or output was already seen
    if help_text.startswith("Error:") or help_text in visited_outputs:
        return results

    visited_outputs.add(help_text)

    for sub in extract_subcommands(help_text, cmd_name=cmd[-1]):
        find_subcommands([*cmd, sub], results, visited_outputs=visited_outputs)

    return results


def format_help_block(tree: dict[str, str]) -> str:
    """Format extracted help tree into a clean text block for prompt injection."""
    blocks: list[str] = []
    for cmd_header, help_content in tree.items():
        blocks.append(f"{cmd_header}\n{help_content}\n")
    return "\n".join(blocks).strip()
