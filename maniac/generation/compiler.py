"""Manpage roff compilation and provenance header injection via Pandoc."""

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from ..config import Config
from ..lifecycle import PROVENANCE_SIGNATURE
from ..logging import logger


def build_provenance_header(tool_name: str, model: str | None = None) -> str:
    """Construct structured provenance comment header for roff files."""
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    model_str = f" | Model: {model}" if model else ""
    return (
        f'{PROVENANCE_SIGNATURE}\n.\\" Tool: {tool_name} | Date: {now_str}{model_str}\n'
    )


def compile_to_man(
    markdown_text: str,
    output_file: str | Path,
    tool_name: str | None = None,
    model: str | None = None,
    timeout: int | None = None,
    config: Config | None = None,
) -> bool:
    """Compile Markdown manpage to roff format using pandoc with provenance header."""
    cfg = config or Config()
    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pandoc_bin = shutil.which("pandoc")
    if not pandoc_bin:
        logger.warning(
            "pandoc is not installed or not in PATH. Skipping roff compilation",
            file=out_path.name,
        )
        return False

    effective_timeout = timeout if timeout is not None else cfg.timeout_pandoc
    try:
        res = subprocess.run(
            [
                pandoc_bin,
                "-s",
                "-f",
                "markdown-smart",
                "-t",
                "man",
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
