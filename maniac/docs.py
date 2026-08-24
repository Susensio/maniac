import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from maniac.discovery import RepoSource

DOC_EXTENSIONS = {".md", ".markdown", ".rst", ".1", ".txt"}
DOC_DIRS = {"doc", "docs", "manual", "book", "man", "manpage", "site"}
IGNORE_DIRS = {
    ".git",
    ".github",
    ".venv",
    "tests",
    "test",
    "crates",
    "assets",
    "target",
    "vendor",
    "node_modules",
    "packages",
    "__pycache__",
}


@dataclass
class DocFile:
    rel_path: str
    content: str


def fetch_and_extract_docs(
    source: RepoSource,
    cache_dir: str | Path = "data/repos",
) -> list[DocFile]:
    """Fetch repository (if remote and not cached) and extract all documentation files."""
    cache_dir_path = Path(cache_dir)
    cache_dir_path.mkdir(parents=True, exist_ok=True)

    repo_dir_name = source.name
    dest_dir = cache_dir_path / repo_dir_name

    # 1. Obtain repo on disk
    if source.is_local and source.local_path:
        target_path = source.local_path
    else:
        # If directory exists but missing .git, clean it up
        if dest_dir.exists() and not (dest_dir / ".git").exists():
            shutil.rmtree(dest_dir, ignore_errors=True)

        if not dest_dir.exists():
            clone_url = source.clone_url
            if not clone_url:
                logger.warning("No clone URL for {}", source.name)
                return []
            logger.info("Cloning {} to {}", clone_url, dest_dir)
            cmd = ["git", "clone", "--depth", "1", clone_url, str(dest_dir)]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if res.returncode != 0:
                logger.error("Failed cloning {}: {}", clone_url, res.stderr.strip())
                shutil.rmtree(dest_dir, ignore_errors=True)
                return []
        target_path = dest_dir

    # 2. Extract documentation files
    return extract_docs_from_dir(target_path)


def extract_docs_from_dir(directory: Path) -> list[DocFile]:
    """Extract documentation files from a local repository directory."""
    if not directory.exists():
        return []

    doc_files: list[DocFile] = []
    seen_rel_paths: set[str] = set()

    # Collect root doc candidates
    for item in sorted(directory.iterdir()):
        if item.is_file():
            name_lower = item.name.lower()
            if name_lower.startswith("readme") or name_lower in {
                "usage.md",
                "architecture.md",
                "configurations.md",
                "design.md",
                "shellcheck.1.md",
            }:
                _read_and_append(item, directory, doc_files, seen_rel_paths)

    # Walk doc subdirectories
    for root, dirs, files in os.walk(directory):
        # Prune ignored directories
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in IGNORE_DIRS]
        rel_dir = os.path.relpath(root, directory)
        first_seg = rel_dir.split(os.sep)[0].lower()
        if first_seg in DOC_DIRS:
            for f in sorted(files):
                ext = os.path.splitext(f)[1].lower()
                if ext in DOC_EXTENSIONS and not f.startswith("."):
                    file_path = Path(root) / f
                    _read_and_append(file_path, directory, doc_files, seen_rel_paths)

    return doc_files


def _read_and_append(
    file_path: Path,
    base_dir: Path,
    doc_files: list[DocFile],
    seen: set[str],
) -> None:
    rel = str(file_path.relative_to(base_dir))
    if rel in seen:
        return
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace").strip()
        if content:
            doc_files.append(DocFile(rel_path=rel, content=content))
            seen.add(rel)
    except (OSError, UnicodeDecodeError) as e:
        logger.debug("Error reading doc file {}: {}", file_path, e)


def format_docs_section(doc_files: list[DocFile]) -> str:
    """Format documentation files into markdown sections."""
    sections: list[str] = []
    for df in doc_files:
        sections.append(f"### {df.rel_path}\n\n{df.content}\n")
    return "\n".join(sections).strip()
