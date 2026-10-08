"""Manpage roff compilation and provenance header injection via Pandoc."""

import re
import shutil
import subprocess
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from ..config import Config
from ..exceptions import UnsupportedPandoc
from ..logging import logger
from ..manifest import PROVENANCE_SIGNATURE

# The first pandoc whose man writer escapes `-` as `\-` (bisected against
# release binaries 3.1.3-3.1.11, ADR-0064).
MIN_PANDOC_VERSION = (3, 1, 10)
_PANDOC_VERSION_TIMEOUT = 10


@cache
def _pandoc_version_line(pandoc_bin: str) -> str:
    """`pandoc --version`'s first line, asked once per binary per process."""
    try:
        result = subprocess.run(
            [pandoc_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=_PANDOC_VERSION_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return f"{pandoc_bin} (--version failed: {e})"
    lines = result.stdout.strip().splitlines()
    return (
        lines[0].strip()
        if result.returncode == 0 and lines
        else (f"{pandoc_bin} (--version exited {result.returncode})")
    )


def require_supported_pandoc() -> str | None:
    """Path to a pandoc new enough to compile with, None when none is installed.

    Raises `UnsupportedPandoc` for a pandoc older than `MIN_PANDOC_VERSION`,
    or one whose version cannot be read (ADR-0060: refused, not guessed).
    A missing pandoc stays None: callers already report that case.
    """
    pandoc_bin = shutil.which("pandoc")
    if pandoc_bin is None:
        return None
    line = _pandoc_version_line(pandoc_bin)
    match = re.match(r"pandoc\S*\s+(\d+(?:\.\d+)*)", line)
    if match is None:
        raise UnsupportedPandoc(line)
    version = tuple(int(part) for part in match.group(1).split("."))
    if version < MIN_PANDOC_VERSION:
        raise UnsupportedPandoc(f"pandoc {match.group(1)}")
    return pandoc_bin


def build_provenance_header(tool_name: str, model: str | None = None) -> str:
    """Construct structured provenance comment header for roff files."""
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    model_str = f" | Model: {model}" if model else ""
    return (
        f'{PROVENANCE_SIGNATURE}\n.\\" Tool: {tool_name} | Date: {now_str}{model_str}\n'
    )


def version_footer(tool_name: str, version: str, *, verbatim: bool) -> str:
    """The `.TH` footer for a page documenting `tool_name` at `version`.

    A provider's version is a bare number, so the footer names the tool in
    front of it (`gh 2.63.0`). An unclaimed binary's version is its own
    `--version` output, kept verbatim (ADR-0020): it already names itself,
    and may run to several lines (gcc's adds a copyright notice), so its
    first line is the footer as it stands.
    """
    if not verbatim:
        return f"{tool_name} {version}"
    lines = [line.strip() for line in version.splitlines() if line.strip()]
    return lines[0] if lines else tool_name


def compile_to_man(
    markdown_text: str,
    output_file: str | Path,
    tool_name: str | None = None,
    model: str | None = None,
    footer: str | None = None,
    timeout: int | None = None,
    config: Config | None = None,
) -> bool:
    """Compile Markdown manpage to roff format using pandoc with provenance header.

    `footer`, when given, becomes pandoc's `footer` metadata -- the 4th
    field of `.TH`, where `git`, `coreutils` and `gh` each put their own
    name and version on this machine (`version_footer` builds it). Left
    unset, pandoc leaves the field empty.
    """
    cfg = config or Config()
    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pandoc_bin = require_supported_pandoc()
    if not pandoc_bin:
        logger.warning(
            "pandoc is not installed or not in PATH. Skipping roff compilation",
            file=out_path.name,
        )
        return False

    effective_timeout = timeout if timeout is not None else cfg.timeout_pandoc
    footer_args = ["--metadata", f"footer={footer}"] if footer else []
    try:
        res = subprocess.run(
            [
                pandoc_bin,
                "-s",
                "-f",
                "markdown-smart",
                "-t",
                "man",
                *footer_args,
                "-o",
                str(out_path),
            ],
            input=markdown_text,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            check=False,
        )
        if res.returncode != 0:
            logger.error("pandoc compilation failed", error=res.stderr.strip())
            return False

        header = build_provenance_header(
            tool_name=tool_name or out_path.stem, model=model
        )
        current_content = out_path.read_text(encoding="utf-8", errors="replace")
        if not current_content.startswith(PROVENANCE_SIGNATURE):
            out_path.write_text(header + current_content, encoding="utf-8")

        logger.info("Compiled roff manpage with provenance", path=str(out_path))
        return True
    except (OSError, subprocess.SubprocessError) as e:
        logger.error("Error compiling manpage with pandoc", error=str(e))
        return False
