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
    "dist",
}

# Non-user documentation to skip
IGNORE_FILE_PATTERNS = {
    "contributing",
    "changelog",
    "code_of_conduct",
    "code-of-conduct",
    "governance",
    "security",
    "benchmarks",
    "roadmap",
    "license",
    "licenses",
    "releases",
    "pull_request_template",
    "issue_template",
    "dependabot",
    "renovate",
}

# Max total characters of documentation to keep synthesis fast and high-signal
MAX_TOTAL_DOC_CHARS = 75_000


@dataclass
class DocFile:
    rel_path: str
    content: str


def fetch_and_extract_docs(
    source: RepoSource,
    cache_dir: str | Path = "data/repos",
) -> list[DocFile]:
    """Fetch repository (if remote and not cached) and extract prioritized documentation files."""
    cache_dir_path = Path(cache_dir)
    cache_dir_path.mkdir(parents=True, exist_ok=True)

    dest_dir = cache_dir_path / source.name

    if source.is_local and source.local_path:
        target_path = source.local_path
    else:
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

    return extract_docs_from_dir(target_path)


def extract_docs_from_dir(directory: Path) -> list[DocFile]:
    """Extract documentation files from a local repository directory, sorted by relevance."""
    if not directory.exists():
        return []

    raw_candidates: list[tuple[int, Path]] = []
    seen_rel_paths: set[str] = set()

    # 1. Collect root doc candidates
    for item in sorted(directory.iterdir()):
        if item.is_file():
            name_lower = item.name.lower()
            if _is_ignored_file(name_lower):
                continue
            if name_lower.startswith("readme") or name_lower in {
                "usage.md",
                "architecture.md",
                "configurations.md",
                "design.md",
                "shellcheck.1.md",
            }:
                prio = 0 if name_lower.startswith("readme") else 1
                raw_candidates.append((prio, item))
                seen_rel_paths.add(str(item.relative_to(directory)))

    # 2. Walk doc subdirectories
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in IGNORE_DIRS]
        rel_dir = os.path.relpath(root, directory)
        first_seg = rel_dir.split(os.sep)[0].lower()
        if first_seg in DOC_DIRS:
            for f in sorted(files):
                ext = os.path.splitext(f)[1].lower()
                name_stem = os.path.splitext(f)[0].lower()
                if (
                    ext in DOC_EXTENSIONS
                    and not f.startswith(".")
                    and not _is_ignored_file(name_stem)
                ):
                    file_path = Path(root) / f
                    rel = str(file_path.relative_to(directory))
                    if rel not in seen_rel_paths:
                        prio = _compute_doc_priority(rel)
                        raw_candidates.append((prio, file_path))
                        seen_rel_paths.add(rel)

    # Sort by priority (lowest number = highest priority)
    raw_candidates.sort(key=lambda x: (x[0], x[1].name))

    doc_files: list[DocFile] = []
    total_chars = 0

    for _, file_path in raw_candidates:
        if total_chars >= MAX_TOTAL_DOC_CHARS:
            break
        rel = str(file_path.relative_to(directory))
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                # Truncate single huge files if needed
                if len(content) > 50_000:
                    content = content[:50_000] + "\n\n[... truncated ...]"
                doc_files.append(DocFile(rel_path=rel, content=content))
                total_chars += len(content)
        except (OSError, UnicodeDecodeError) as e:
            logger.debug("Error reading doc file {}: {}", file_path, e)

    return doc_files


def _is_ignored_file(name: str) -> bool:
    for pat in IGNORE_FILE_PATTERNS:
        if pat in name:
            return True
    return False


def _compute_doc_priority(rel_path: str) -> int:
    path_lower = rel_path.lower()
    if (
        "cli" in path_lower
        or "reference" in path_lower
        or "manual" in path_lower
        or "usage" in path_lower
    ):
        return 1
    if (
        "guide" in path_lower
        or "concept" in path_lower
        or "getting-started" in path_lower
        or "book/src" in path_lower
    ):
        return 2
    if "config" in path_lower or "setting" in path_lower or "rules" in path_lower:
        return 3
    return 4


def format_docs_section(doc_files: list[DocFile]) -> str:
    """Format documentation files into markdown sections."""
    sections: list[str] = []
    for df in doc_files:
        sections.append(f"### {df.rel_path}\n\n{df.content}\n")
    return "\n".join(sections).strip()
