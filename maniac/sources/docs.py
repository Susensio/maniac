"""Repository fetching and documentation extraction."""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

from ..config import Config
from ..logging import logger
from ..models import DocFile, RepoSource

DOC_EXTENSIONS = {".md", ".markdown", ".rst", ".1", ".txt"}
DOC_DIRS = {
    "doc",
    "docs",
    "documentation",
    "manual",
    "manuals",
    "book",
    "man",
    "manpage",
    "manpages",
    "site",
    "wiki",
}
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
    "release-notes",
    "pull_request_template",
    "issue_template",
    "dependabot",
    "renovate",
    "building-from-source",
    "package-managers",
}

MAX_TOTAL_DOC_CHARS = 75_000


def fetch_and_extract_docs(
    source: RepoSource,
    cache_dir: str | Path | None = None,
    max_total_chars: int = MAX_TOTAL_DOC_CHARS,
    config: Config | None = None,
) -> list[DocFile]:
    """Fetch repository (if remote and not cached) and extract prioritized documentation files."""
    cfg = config or Config()
    if source.is_local and source.local_path:
        target_path = source.local_path
    else:
        cache_dir_path = Path(cache_dir) if cache_dir is not None else cfg.cache_dir
        cache_dir_path.mkdir(parents=True, exist_ok=True)
        dest_dir = cache_dir_path / source.name

        if dest_dir.exists() and not (dest_dir / ".git").exists():
            shutil.rmtree(dest_dir, ignore_errors=True)

        if not dest_dir.exists():
            clone_url = source.clone_url
            if not clone_url:
                logger.debug("No clone URL for repository", source=source.name)
                return []
            logger.info("Cloning repository", url=clone_url, dest=str(dest_dir))
            cmd = ["git", "clone", "--depth", "1", clone_url, str(dest_dir)]
            try:
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=cfg.timeout_git,
                    check=False,
                )
                if res.returncode != 0:
                    logger.error(
                        "Failed cloning repository",
                        url=clone_url,
                        error=res.stderr.strip(),
                    )
                    shutil.rmtree(dest_dir, ignore_errors=True)
                    return []
            except (OSError, subprocess.SubprocessError) as e:
                logger.error("Error executing git clone", url=clone_url, error=str(e))
                shutil.rmtree(dest_dir, ignore_errors=True)
                return []
        target_path = dest_dir

    return extract_docs_from_dir(target_path, max_total_chars=max_total_chars)


def extract_docs_from_dir(
    directory: Path,
    max_total_chars: int = MAX_TOTAL_DOC_CHARS,
) -> list[DocFile]:
    """Extract documentation files from a local repository directory, sorted by relevance."""
    if not directory.exists():
        return []

    raw_candidates: list[tuple[int, Path]] = []
    seen_rel_paths: set[str] = set()

    for item in sorted(directory.iterdir()):
        if not item.is_file():
            continue
        name_lower = item.name.lower()
        ext = item.suffix.lower()
        if (
            _is_ignored_file(name_lower)
            or ext not in DOC_EXTENSIONS
            or item.name.startswith(".")
        ):
            continue
        rel = str(item.relative_to(directory))
        prio = 0 if name_lower.startswith("readme") else _compute_doc_priority(rel)
        raw_candidates.append((prio, item))
        seen_rel_paths.add(rel)

    for rel, file_path in _iter_doc_dir_files(directory):
        if rel in seen_rel_paths:
            continue
        prio = _compute_doc_priority(rel)
        raw_candidates.append((prio, file_path))
        seen_rel_paths.add(rel)

    raw_candidates.sort(key=lambda x: (x[0], x[1].name))

    doc_files: list[DocFile] = []
    total_chars = 0

    for _, file_path in raw_candidates:
        if total_chars >= max_total_chars:
            break
        rel = str(file_path.relative_to(directory))
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                if len(content) > 50_000:
                    content = content[:50_000] + "\n\n[... truncated ...]"
                doc_files.append(DocFile(rel_path=rel, content=content))
                total_chars += len(content)
        except OSError as e:
            logger.debug("Error reading doc file", path=str(file_path), error=str(e))

    return doc_files


def _iter_doc_dir_files(directory: Path) -> Iterator[tuple[str, Path]]:
    """Yield (rel_path, file_path) for candidate doc files under known doc dirs."""
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in IGNORE_DIRS]
        rel_dir = os.path.relpath(root, directory)
        first_seg = rel_dir.split(os.sep)[0].lower()
        if first_seg not in DOC_DIRS:
            continue
        for f in sorted(files):
            if not _is_candidate_doc_filename(f):
                continue
            file_path = Path(root) / f
            yield str(file_path.relative_to(directory)), file_path


def _is_candidate_doc_filename(name: str) -> bool:
    if name.startswith("."):
        return False
    ext = os.path.splitext(name)[1].lower()
    return ext in DOC_EXTENSIONS and not _is_ignored_file(name.lower())


def _is_ignored_file(name: str) -> bool:
    for pat in IGNORE_FILE_PATTERNS:
        if pat in name:
            return True
    return False


def _compute_doc_priority(rel_path: str) -> int:
    path_lower = rel_path.lower()
    if any(
        k in path_lower
        for k in (
            "keymap",
            "keys",
            "shortcut",
            "key-binding",
            "cli",
            "reference",
            "manual",
            "usage",
            "commands",
        )
    ):
        return 1
    if any(
        k in path_lower
        for k in (
            "guide",
            "concept",
            "getting-started",
            "editor",
            "configuration",
            "config",
            "setting",
            "rule",
        )
    ):
        return 2
    if any(
        k in path_lower
        for k in ("mode", "surround", "textobject", "register", "jumplist")
    ):
        return 3
    return 4


def format_docs_section(doc_files: list[DocFile]) -> str:
    """Format documentation files into markdown sections."""
    sections: list[str] = []
    for df in doc_files:
        sections.append(f"### {df.rel_path}\n\n{df.content}\n")
    return "\n".join(sections).strip()
