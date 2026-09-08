"""Repository fetching and documentation extraction."""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

from ..config import Config
from ..logging import logger
from ..models import DocFile, RepoSource
from .manpages import find_repo_manpage as _find_repo_manpage
from .manpages import is_help2man_content

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
TRUNCATION_MARKER = "\n\n[... truncated ...]"


def fetch_and_extract_docs(
    source: RepoSource,
    cache_dir: str | Path | None = None,
    max_total_chars: int = MAX_TOTAL_DOC_CHARS,
    config: Config | None = None,
) -> list[DocFile]:
    """Fetch repository (if remote and not cached) and extract prioritized documentation files."""
    cfg = config or Config()
    cache_dir_path = Path(cache_dir) if cache_dir is not None else cfg.cache_dir
    target_path = resolve_repo_dir(source, cache_dir_path, cfg)
    if target_path is None:
        return []

    doc_files = extract_docs_from_dir(target_path, max_total_chars=max_total_chars)
    if source.is_local:
        return doc_files

    remaining_chars = max_total_chars - sum(len(doc.content) for doc in doc_files)
    if remaining_chars <= 0:
        return doc_files

    return doc_files + _fetch_github_wiki_docs(
        source, cache_dir_path, cfg, remaining_chars
    )


def resolve_repo_dir(
    source: RepoSource,
    cache_dir_path: Path,
    cfg: Config,
    version: str | None = None,
) -> Path | None:
    """Resolve a source to its local directory, cloning a remote repository if needed.

    With `version`, clones the git tag naming it instead of the default
    branch, into a distinct `<name>@<tag>` directory -- ADR-0016 tier 2
    needs the installed version, not whatever the default branch currently
    holds, and a page fetched at the wrong version is worse than none.
    Returns None, rather than falling back to the default branch, when no
    tag matches.
    """
    if source.is_local and source.local_path:
        return source.local_path

    cache_dir_path.mkdir(parents=True, exist_ok=True)

    ref = None
    dest_name = source.name
    if version is not None:
        clone_url = source.clone_url
        if not clone_url:
            logger.debug("No clone URL for repository", source=source.name)
            return None
        ref = _find_matching_tag(clone_url, version, cfg.timeout_git)
        if ref is None:
            logger.debug(
                "No matching upstream tag for installed version",
                source=source.name,
                version=version,
            )
            return None
        dest_name = f"{source.name}@{ref}"

    dest_dir = cache_dir_path / dest_name
    if dest_dir.exists() and not (dest_dir / ".git").exists():
        shutil.rmtree(dest_dir, ignore_errors=True)

    if not dest_dir.exists():
        clone_url = source.clone_url
        if not clone_url:
            logger.debug("No clone URL for repository", source=source.name)
            return None
        if not _clone_repository(clone_url, dest_dir, cfg, ref=ref):
            return None
    return dest_dir


def discover_repo_manpage(
    source: RepoSource,
    binary_name: str,
    cache_dir: str | Path | None = None,
    config: Config | None = None,
    version: str | None = None,
) -> Path | None:
    """Return a hand-authored manpage for ``binary_name`` shipped in ``source``'s repository.

    Resolves (and clones, if needed and not already cached) the same repository
    directory ``fetch_and_extract_docs`` uses, then looks for a manpage MANIAC
    can install as-is instead of generating one. With `version`, resolves the
    git tag naming it (ADR-0016 tier 2) rather than the default branch.
    """
    cfg = config or Config()
    cache_dir_path = Path(cache_dir) if cache_dir is not None else cfg.cache_dir
    repo_dir = resolve_repo_dir(source, cache_dir_path, cfg, version=version)
    if repo_dir is None:
        return None
    return _find_repo_manpage(repo_dir, binary_name)


def _find_matching_tag(clone_url: str, version: str, timeout: int) -> str | None:
    """Return the git tag naming `version`, or None if none does.

    Tries the two conventional spellings, `v<version>` then a bare
    `<version>`, against the remote's tag list via `git ls-remote` -- no
    clone needed to check. Covers semver-tagged projects (`gh` tags
    `v2.90.0`, `pandoc` tags `3.10.2`) without guessing at less common
    schemes; deliberately not a fuzzy match, since an unmatched version is
    refused rather than approximated (ADR-0016).
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", clone_url],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("Error listing remote tags", url=clone_url, error=str(e))
        return None
    if result.returncode != 0:
        return None

    tags: set[str] = set()
    for line in result.stdout.splitlines():
        _, _, ref = line.partition("refs/tags/")
        if ref:
            tags.add(ref.removesuffix("^{}"))

    for candidate in (f"v{version}", version):
        if candidate in tags:
            return candidate
    return None


def _clone_repository(
    clone_url: str,
    dest_dir: Path,
    cfg: Config,
    *,
    ref: str | None = None,
    optional: bool = False,
) -> bool:
    """Clone a shallow repository into dest_dir, at `ref` (a tag or branch) if given."""
    logger.info("Cloning repository", url=clone_url, dest=str(dest_dir), ref=ref)
    cmd = ["git", "clone", "--depth", "1"]
    if ref is not None:
        cmd += ["--branch", ref]
    cmd += [clone_url, str(dest_dir)]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=cfg.timeout_git,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log = logger.debug if optional else logger.error
        log("Error executing git clone", url=clone_url, error=str(e))
        shutil.rmtree(dest_dir, ignore_errors=True)
        return False

    if res.returncode == 0:
        return True

    log = logger.debug if optional else logger.error
    log("Failed cloning repository", url=clone_url, error=res.stderr.strip())
    shutil.rmtree(dest_dir, ignore_errors=True)
    return False


def _fetch_github_wiki_docs(
    source: RepoSource,
    cache_dir: Path,
    cfg: Config,
    max_total_chars: int,
) -> list[DocFile]:
    """Fetch documentation from a GitHub wiki when one exists."""
    clone_url = source.clone_url
    if clone_url is None or not clone_url.startswith("https://github.com/"):
        return []

    wiki_dir = cache_dir / f"{source.name}.wiki"
    if wiki_dir.exists() and not (wiki_dir / ".git").exists():
        shutil.rmtree(wiki_dir, ignore_errors=True)

    if not wiki_dir.exists():
        wiki_url = f"{clone_url.removesuffix('.git')}.wiki.git"
        if not _clone_repository(wiki_url, wiki_dir, cfg, optional=True):
            return []

    return [
        DocFile(rel_path=f"wiki/{doc.rel_path}", content=doc.content)
        for doc in extract_docs_from_dir(wiki_dir, max_total_chars=max_total_chars)
    ]


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
                if file_path.suffix == ".1" and is_help2man_content(content):
                    logger.debug("Skipping help2man-generated manpage", path=rel)
                    continue
                if len(content) > 50_000:
                    content = content[:50_000] + TRUNCATION_MARKER
                content = _truncate_doc_content(content, max_total_chars - total_chars)
                doc_files.append(DocFile(rel_path=rel, content=content))
                total_chars += len(content)
        except OSError as e:
            logger.debug("Error reading doc file", path=str(file_path), error=str(e))

    return doc_files


def _truncate_doc_content(content: str, max_chars: int) -> str:
    """Truncate content without exceeding max_chars."""
    if len(content) <= max_chars:
        return content
    if max_chars <= len(TRUNCATION_MARKER):
        return content[:max_chars]
    return content[: max_chars - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


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
