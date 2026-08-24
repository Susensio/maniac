import os
import shutil
import subprocess
from pathlib import Path

from loguru import logger


def compile_to_man(markdown_text: str, output_file: str | Path) -> bool:
    """Compile Markdown manpage to roff format using pandoc."""
    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pandoc_bin = shutil.which("pandoc")
    if not pandoc_bin:
        logger.warning(
            "pandoc is not installed or not in PATH. Skipping roff compilation for {}.",
            out_path.name,
        )
        return False

    try:
        res = subprocess.run(
            [pandoc_bin, "-s", "-t", "man", "-o", str(out_path)],
            input=markdown_text,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if res.returncode == 0:
            logger.info("Compiled roff manpage: {}", out_path)
            return True
        logger.error("pandoc compilation failed: {}", res.stderr.strip())
        return False
    except (OSError, subprocess.SubprocessError) as e:
        logger.error("Error compiling manpage with pandoc: {}", e)
        return False


def install_manpage(
    source_file: str | Path,
    target_dir: str | Path = "~/.local/share/man/man1",
) -> Path:
    """Copy compiled roff manpage into local man directory."""
    src = Path(source_file)
    dest_dir = Path(os.path.expanduser(str(target_dir)))
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_file = dest_dir / src.name
    shutil.copy2(src, dest_file)
    logger.info("Installed manpage to {}", dest_file)
    return dest_file
