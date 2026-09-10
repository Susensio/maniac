"""CLI help crawling and subcommand discovery."""

import os
import re
import subprocess
from dataclasses import dataclass

from ..config import Config
from ..exceptions import CrawlerError
from ..logging import logger

# Box-drawing and border characters for normalization
_BORDER_PATTERN = re.compile(r"^[╭╮╯╰┌┐└┘│─━═┏┓┗┛+\|\s]+|[╭╮╯╰┌┐└┘│─━═┏┓┗┛+\|\s]+$")
_DIVIDER_PATTERN = re.compile(r"^[-=─━═+|╭╮╯╰┌┐└┘│┏┓┗┛\s]+$")
_BOX_START_PATTERN = re.compile(r"^[╭┌┏+]\s*[-─━=]")
_VERTICAL_BORDER_LEFT = re.compile(r"^[│|]")
_VERTICAL_BORDER_RIGHT = re.compile(r"[│|]\s*$")
_HEADER_KEYWORDS = ("command", "subcommand", "action")
_KNOWN_SECTION_NAMES = {
    "commands",
    "subcommands",
    "available commands",
    "actions",
    "available actions",
    "options",
    "flags",
    "arguments",
    "usage",
    "examples",
}
_SUBCOMMAND_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$")


@dataclass(slots=True)
class _NormalizedLine:
    text: str
    indent: int
    is_divider: bool = False


def _normalize_help_text(text: str) -> list[_NormalizedLine]:
    """Pass 1: Strip box borders and calculate indentation per line."""
    normalized: list[_NormalizedLine] = []

    for raw_line in text.splitlines():
        if not raw_line.strip():
            normalized.append(_NormalizedLine(text="", indent=0, is_divider=True))
            continue

        if _DIVIDER_PATTERN.fullmatch(raw_line):
            normalized.append(_NormalizedLine(text="", indent=0, is_divider=True))
            continue

        if _BOX_START_PATTERN.match(raw_line.strip()):
            cleaned = _BORDER_PATTERN.sub("", raw_line).strip()
            normalized.append(_NormalizedLine(text=cleaned, indent=0))
            continue

        if _VERTICAL_BORDER_LEFT.match(raw_line.lstrip()):
            stripped_left = _VERTICAL_BORDER_LEFT.sub("", raw_line.lstrip())
            stripped_both = _VERTICAL_BORDER_RIGHT.sub("", stripped_left).rstrip()
            indent = len(stripped_both) - len(stripped_both.lstrip())
            normalized.append(
                _NormalizedLine(text=stripped_both.strip(), indent=max(indent, 1))
            )
            continue

        stripped = raw_line.rstrip()
        indent = len(stripped) - len(stripped.lstrip())
        normalized.append(_NormalizedLine(text=stripped.strip(), indent=indent))

    return normalized


def _is_section_header(line: _NormalizedLine) -> tuple[bool, bool]:
    """Detect if a normalized line is a section header.

    Returns (is_header, is_command_section).
    """
    text = line.text
    if not text:
        return False, False

    text_lower = text.lower().rstrip(":")
    is_header = False

    if line.indent <= 2 and (
        text.endswith(":")
        or (text.isupper() and len(text) >= 3)
        or text_lower in _KNOWN_SECTION_NAMES
    ):
        is_header = True

    if not is_header:
        return False, False

    is_cmd_section = any(kw in text_lower for kw in _HEADER_KEYWORDS)
    return True, is_cmd_section


def extract_subcommands(text: str, cmd_name: str | None = None) -> list[str]:
    """Extract subcommand names from structured CLI help text."""
    normalized_lines = _normalize_help_text(text)
    subcommands: list[str] = []
    in_commands = False

    for line in normalized_lines:
        if line.is_divider or not line.text:
            continue

        is_header, is_cmd_section = _is_section_header(line)
        if is_header:
            in_commands = is_cmd_section
            continue

        if not in_commands:
            continue

        if line.indent > 4 or line.indent == 0:
            continue

        parts = re.split(r"\s{2,}|\t+|\s*\|\s*", line.text, maxsplit=1)
        tokens = parts[0].split()
        if not tokens:
            continue

        if (
            tokens[0].lower() in ("command", "commands", "subcommand", "subcommands")
            and len(tokens) == 1
            and len(parts) > 1
            and parts[1]
            .lstrip("| \t")
            .lower()
            .startswith(("desc", "summary", "help", "action"))
        ):
            continue

        idx = 1 if (cmd_name and tokens[0] == cmd_name and len(tokens) > 1) else 0
        cmd_token = tokens[idx].rstrip(":,")

        if _SUBCOMMAND_PATTERN.match(cmd_token):
            subcommands.append(cmd_token)

    return subcommands


def _run_cli_flag(
    cmd: list[str], flag: str, timeout: int
) -> subprocess.CompletedProcess[str]:
    """Run `cmd` with `flag` appended, pager- and color-stripped, and return the result.

    Raises `subprocess.TimeoutExpired` or `OSError` verbatim -- callers
    decide what a timeout or a missing executable means for them.
    """
    full_cmd = [*cmd, flag]
    cmd_str = " ".join(full_cmd)
    logger.debug("Executing command", command=cmd_str)
    env = os.environ | {
        "PAGER": "cat",
        "BAT_PAGER": "",
        "GIT_PAGER": "",
        "SYSTEMD_PAGER": "cat",
        "NO_COLOR": "1",
        "TERM": "dumb",
    }
    return subprocess.run(
        full_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        errors="replace",
        env=env,
        check=False,
    )


def get_help(
    cmd: list[str],
    timeout: int | None = None,
    config: Config | None = None,
) -> str:
    """Execute command with --help and capture standard output."""
    cfg = config or Config()
    effective_timeout = timeout if timeout is not None else cfg.timeout_help
    cmd_str = " ".join([*cmd, "--help"])
    try:
        res = _run_cli_flag(cmd, "--help", effective_timeout)
        if res.returncode != 0:
            logger.debug(
                "Command exited with non-zero code",
                command=cmd_str,
                returncode=res.returncode,
            )
        return (res.stdout or "").strip()
    except subprocess.TimeoutExpired as e:
        logger.warning("Command timed out", command=cmd_str)
        raise CrawlerError(f"Command timed out: '{cmd_str}'") from e
    except (FileNotFoundError, OSError) as e:
        logger.warning("Failed to run command", command=cmd_str, error=str(e))
        raise CrawlerError(
            f"Executable '{cmd[0]}' not found on $PATH. "
            f"Verify the command is installed and executable."
        ) from e


def get_version(
    cmd: list[str],
    timeout: int | None = None,
    config: Config | None = None,
) -> str | None:
    """Execute command with --version and return its verbatim output, or `None`.

    Unlike `get_help`, no failure here is an error (ADR-0020): no
    `--version` flag, a non-zero exit, a timeout, a missing executable, and
    empty output are all a tool not reporting a version, not a crawler
    fault, so the caller sees an absence rather than a `CrawlerError`.
    """
    cfg = config or Config()
    effective_timeout = timeout if timeout is not None else cfg.timeout_help
    try:
        res = _run_cli_flag(cmd, "--version", effective_timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0:
        return None
    return (res.stdout or "").strip() or None


def find_subcommands(
    cmd: str | list[str],
    results: dict[str, str] | None = None,
    visited_outputs: set[str] | None = None,
    timeout: int | None = None,
    config: Config | None = None,
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
        help_text = get_help(cmd, timeout=timeout, config=config)
    except CrawlerError:
        if not results:
            raise
        return results

    results[key] = help_text

    if help_text.startswith("Error:") or help_text in visited_outputs:
        return results

    visited_outputs.add(help_text)

    for sub in extract_subcommands(help_text, cmd_name=cmd[-1]):
        find_subcommands(
            [*cmd, sub],
            results,
            visited_outputs=visited_outputs,
            timeout=timeout,
            config=config,
        )

    return results


def format_help_block(tree: dict[str, str]) -> str:
    """Format extracted help tree into a clean text block for prompt injection."""
    blocks: list[str] = []
    for cmd_header, help_content in tree.items():
        blocks.append(f"{cmd_header}\n{help_content}\n")
    return "\n".join(blocks).strip()
