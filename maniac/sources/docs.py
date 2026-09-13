"""Repository fetching and documentation extraction."""

import bz2
import fcntl
import gzip
import json
import lzma
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from fnmatch import fnmatch
from hashlib import sha256
from io import BytesIO, TextIOWrapper
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import zstandard

from ..config import Config
from ..logging import logger
from ..models import DocFile, RepoSource
from .manpages import (
    REPO_MANPAGE_DIRS,
    is_help2man_content,
    manpage_documents,
)
from .manpages import find_repo_manpage as _find_repo_manpage

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
_RELEASE_ARCHIVE_LIMIT = 10 * 1024 * 1024
_RELEASE_ARCHIVE_CANDIDATE_LIMIT = 256 * 1024
_RELEASE_MEMBER_LIMIT = 2 * 1024 * 1024
_RELEASE_EXTRACTED_LIMIT = 8 * 1024 * 1024
_MAN_ASSET_TOKEN = re.compile(
    r"(?:^|[-_.])man(?:page|pages)?(?:[-_.]|$)", re.IGNORECASE
)
_NEGATIVE_CACHE_TTL = 5 * 60
_CACHE_MAX_BYTES = 8 * 1024
_cache_locks: dict[Path, threading.Lock] = {}
_cache_locks_guard = threading.Lock()
_lookup_state = threading.local()


@dataclass(frozen=True)
class _ProbeResult:
    """Probe pages alongside whether every applicable source completed."""

    pages: list[Path]
    definitive: bool


def fetch_and_extract_docs(
    source: RepoSource,
    cache_dir: str | Path | None = None,
    max_total_chars: int = MAX_TOTAL_DOC_CHARS,
    config: Config | None = None,
    version: str | None = None,
) -> tuple[list[DocFile], bool]:
    """Fetch repository (if remote and not cached) and extract prioritized documentation files.

    Returns the docs alongside whether they are version-matched. With
    `version`, tries `resolve_repo_dir` at the tag naming it first (ADR-0019,
    same path ADR-0016 tier 2 uses); the wiki (`_fetch_github_wiki_docs`)
    stays in the context either way, since it carries no version of its own
    to disagree with. Falling back to the default branch on no match, rather
    than returning no docs, is what tier 2 refuses and tier 3 -- the last
    resort -- does not: the page is still worth having, only the version
    claim is not.
    """
    cfg = config or Config()
    cache_dir_path = Path(cache_dir) if cache_dir is not None else cfg.cache_dir
    target_path = resolve_repo_dir(source, cache_dir_path, cfg, version=version)
    matched = version is not None and target_path is not None
    if target_path is None and version is not None:
        target_path = resolve_repo_dir(source, cache_dir_path, cfg)
    if target_path is None:
        return [], False

    doc_files = extract_docs_from_dir(target_path, max_total_chars=max_total_chars)
    if source.is_local:
        return doc_files, matched

    remaining_chars = max_total_chars - sum(len(doc.content) for doc in doc_files)
    if remaining_chars <= 0:
        return doc_files, matched

    return (
        doc_files
        + _fetch_github_wiki_docs(source, cache_dir_path, cfg, remaining_chars),
        matched,
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
    clone_url = source.clone_url
    if not clone_url:
        logger.debug("No clone URL for repository", source=source.name)
        return None

    ref = None
    dest_name = source.name
    if version is not None:
        for candidate in _tag_candidates(version):
            cached_dir = cache_dir_path / f"{source.name}@{candidate}"
            if cached_dir.exists() and _cache_matches_source(
                cached_dir, clone_url, cfg.timeout_git
            ):
                return cached_dir
            if cached_dir.exists():
                shutil.rmtree(cached_dir, ignore_errors=True)

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
    if dest_dir.exists() and not _cache_matches_source(
        dest_dir, clone_url, cfg.timeout_git
    ):
        shutil.rmtree(dest_dir, ignore_errors=True)

    if not dest_dir.exists() and not _clone_repository(
        clone_url, dest_dir, cfg, ref=ref
    ):
        return None
    return dest_dir


def discover_repo_manpage(
    source: RepoSource,
    binary_name: str,
    cache_dir: str | Path | None = None,
    config: Config | None = None,
    version: str | None = None,
) -> Path | None:
    """Return a hand-authored page discovered for ``binary_name`` from ``source``.

    Remote repository checks use cached bare, filtered Git objects and materialize
    only the selected page; GitHub sources also inspect bounded release artifacts.
    With `version`, the Git tree and release tag must name that exact version
    (ADR-0016 tier 2), rather than the default branch.
    """
    pages = discover_repo_manpages(
        source,
        binary_name,
        cache_dir=cache_dir,
        config=config,
        version=version,
    )
    return pages[0] if pages else None


def discovered_manpage_uri(page: Path) -> str | None:
    """Return the upstream-hosted URI recorded when ``page`` was materialized."""
    try:
        metadata = json.loads(
            (page.parent / ".source.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    uri = metadata.get("uri")
    return (
        uri
        if isinstance(uri, str) and uri.startswith(("http://", "https://"))
        else None
    )


def discover_repo_manpages(
    source: RepoSource,
    binary_name: str,
    cache_dir: str | Path | None = None,
    config: Config | None = None,
    version: str | None = None,
) -> list[Path]:
    """Return all safe manpages in a release bundle anchored to ``binary_name``.

    Repository trees remain primary-page-only: unlike a release archive, a
    checkout can contain unrelated documentation trees.  A release bundle is
    accepted only after its primary page proves it documents the binary.
    """
    cfg = config or Config()
    cache_dir_path = Path(cache_dir) if cache_dir is not None else cfg.cache_dir
    if source.is_local:
        if source.local_path is None:
            return []
        page = _find_repo_manpage(source.local_path, binary_name)
        return [page] if page is not None and _valid_page(page, binary_name) else []

    clone_url = source.clone_url
    if clone_url is None:
        return []

    # Versioned upstream results are immutable once published.  Holding this
    # lock across a cold probe makes concurrent list rows share one network trip.
    if version is None:
        return _discover_remote_then_release(
            source, binary_name, cache_dir_path, cfg, version, None
        ).pages
    probe_path = _upstream_cache_path(
        cache_dir_path, "probes", "source-uri-v1", clone_url, version, binary_name
    )
    with _cache_lock(probe_path):
        cached = _read_probe_cache(probe_path, cache_dir_path, binary_name)
        if cached is not None:
            return cached
        tag, tag_definitive = _find_matching_tag_cached_result(
            cache_dir_path, clone_url, version, cfg
        )
        result = _discover_remote_then_release(
            source, binary_name, cache_dir_path, cfg, version, tag
        )
        if result.pages or (tag_definitive and result.definitive):
            _write_probe_cache(probe_path, result.pages)
        return result.pages


def _discover_remote_then_release(
    source: RepoSource,
    binary_name: str,
    cache_dir: Path,
    cfg: Config,
    version: str | None,
    tag: str | None,
) -> _ProbeResult:
    if version is not None and tag is None:
        return _ProbeResult([], True)
    remote = _discover_remote_manpage_result(
        source, binary_name, cache_dir, cfg, version, tag
    )
    if remote.pages:
        return remote
    release = _discover_github_release_manpages_result(
        source, binary_name, cache_dir, cfg, version, tag
    )
    return _ProbeResult(release.pages, remote.definitive and release.definitive)


def _discover_remote_manpage(
    source: RepoSource,
    binary_name: str,
    cache_dir: Path,
    cfg: Config,
    version: str | None,
    tag: str | None = None,
) -> Path | None:
    """Find one page from a bare, filtered object cache; never make a worktree."""
    result = _discover_remote_manpage_result(
        source, binary_name, cache_dir, cfg, version, tag
    )
    return result.pages[0] if result.pages else None


def _discover_remote_manpage_result(
    source: RepoSource,
    binary_name: str,
    cache_dir: Path,
    cfg: Config,
    version: str | None,
    tag: str | None = None,
) -> _ProbeResult:
    """Probe a repository tree and retain whether it was completely inspected."""
    clone_url = source.clone_url
    if clone_url is None:
        return _ProbeResult([], False)
    ref_name = "default"
    fetch_ref = "HEAD"
    source_ref = "HEAD"
    if version is not None:
        tag = tag or _find_matching_tag_cached(cache_dir, clone_url, version, cfg)
        if tag is None:
            return _ProbeResult([], True)
        ref_name = tag
        fetch_ref = f"refs/tags/{tag}"
        source_ref = tag
    repo = _bare_cache_dir(cache_dir, clone_url)
    ref = _fetch_bare_ref(repo, clone_url, ref_name, fetch_ref, cfg)
    if ref is None:
        return _ProbeResult([], False)
    paths = _git_stdout(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", ref], cfg
    )
    if paths is None:
        return _ProbeResult([], False)
    definitive = True
    for path in _matching_manpage_paths(paths.splitlines(), binary_name):
        content = _git_bytes(["git", "-C", str(repo), "show", f"{ref}:{path}"], cfg)
        if content is None:
            definitive = False
            continue
        cached = _materialize_page(cache_dir, source, ref_name, path, content)
        _record_page_uri(cached, _repository_page_uri(source, source_ref, path))
        if _valid_page(cached, binary_name):
            return _ProbeResult([cached], True)
    return _ProbeResult([], definitive)


def _bare_cache_dir(cache_dir: Path, clone_url: str) -> Path:
    digest = sha256(clone_url.encode()).hexdigest()[:16]
    return cache_dir / "git" / f"{digest}.git"


def _fetch_bare_ref(
    repo: Path, clone_url: str, ref_name: str, fetch_ref: str, cfg: Config
) -> str | None:
    with _bare_cache_lock(repo):
        return _fetch_bare_ref_locked(repo, clone_url, ref_name, fetch_ref, cfg)


@contextmanager
def _bare_cache_lock(repo: Path) -> Iterator[None]:
    repo.parent.mkdir(parents=True, exist_ok=True)
    with (repo.parent / f"{repo.name}.lock").open("w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _fetch_bare_ref_locked(
    repo: Path, clone_url: str, ref_name: str, fetch_ref: str, cfg: Config
) -> str | None:
    ref = f"refs/maniac/{sha256(ref_name.encode()).hexdigest()[:16]}"
    if not repo.exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        if _git_stdout(["git", "init", "--bare", str(repo)], cfg) is None:
            return None
        if (
            _git_stdout(
                ["git", "-C", str(repo), "remote", "add", "origin", clone_url], cfg
            )
            is None
        ):
            return None
    remote = _git_stdout(["git", "-C", str(repo), "remote", "get-url", "origin"], cfg)
    if remote is None or remote.strip() != clone_url:
        return None
    if _git_stdout(["git", "-C", str(repo), "rev-parse", "--verify", ref], cfg) is None:
        fetched = _git_stdout(
            [
                "git",
                "-C",
                str(repo),
                "fetch",
                "--depth",
                "1",
                "--filter=blob:none",
                "origin",
                f"{fetch_ref}:{ref}",
            ],
            cfg,
        )
        if fetched is None:
            return None
    return ref


def _git_stdout(cmd: list[str], cfg: Config) -> str | None:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=cfg.timeout_git, check=False
        )
    except (OSError, subprocess.SubprocessError) as error:
        logger.debug("Git command failed", command=cmd[1], error=str(error))
        return None
    return result.stdout if result.returncode == 0 else None


def _git_bytes(cmd: list[str], cfg: Config) -> bytes | None:
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=cfg.timeout_git, check=False
        )
    except (OSError, subprocess.SubprocessError) as error:
        logger.debug("Git command failed", command=cmd[1], error=str(error))
        return None
    return result.stdout if result.returncode == 0 else None


def _matching_manpage_paths(
    paths: Iterator[str] | list[str], binary_name: str
) -> Iterator[str]:
    exact = [
        f"{binary_name}.[1-9]{suffix}" for suffix in ("", ".gz", ".bz2", ".xz", ".zst")
    ]
    nested = [
        f"{binary_name}-*.[1-9]{suffix}"
        for suffix in ("", ".gz", ".bz2", ".xz", ".zst")
    ]
    candidates = sorted(path for path in paths if _path_can_be_repo_manpage(path))
    for patterns in (exact, nested):
        for path in candidates:
            if any(fnmatch(Path(path).name, pattern) for pattern in patterns):
                yield path


def _path_can_be_repo_manpage(path: str) -> bool:
    parts = tuple(part.lower() for part in Path(path).parts)
    return len(parts) == 1 or any(
        parts[: len(prefix)] == prefix for prefix in REPO_MANPAGE_DIRS
    )


def _materialize_page(
    cache_dir: Path, source: RepoSource, ref: str, original_path: str, content: bytes
) -> Path:
    key = sha256(f"{source.target}\0{ref}\0{original_path}".encode()).hexdigest()
    destination = cache_dir / "manpages" / key / Path(original_path).name
    with _cache_lock(destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        temporary.replace(destination)
    return destination


def _repository_page_uri(source: RepoSource, ref: str, path: str) -> str | None:
    """Build the browser URL for an exact file in a GitHub repository ref."""
    clone_url = source.clone_url
    if clone_url is None or not clone_url.startswith("https://github.com/"):
        return None
    repository = clone_url.removeprefix("https://github.com/").removesuffix(".git")
    return (
        f"https://github.com/{repository}/blob/{quote(ref, safe='')}/"
        f"{quote(path, safe='/')}"
    )


def _record_page_uri(page: Path, uri: str | None) -> None:
    if uri is not None:
        _write_json_cache(page.parent / ".source.json", {"uri": uri})


def _discover_github_release_manpages(
    source: RepoSource,
    binary_name: str,
    cache_dir: Path,
    cfg: Config,
    version: str | None,
    tag: str | None = None,
) -> list[Path]:
    return _discover_github_release_manpages_result(
        source, binary_name, cache_dir, cfg, version, tag
    ).pages


def _discover_github_release_manpages_result(
    source: RepoSource,
    binary_name: str,
    cache_dir: Path,
    cfg: Config,
    version: str | None,
    tag: str | None = None,
) -> _ProbeResult:
    """Probe GitHub release assets and retain whether the response was complete."""
    tag_result = _release_tag(source, cache_dir, cfg, version, tag)
    if isinstance(tag_result, _ProbeResult):
        return tag_result
    assets_result = _release_assets(source, cache_dir, cfg, tag_result)
    if isinstance(assets_result, _ProbeResult):
        return assets_result

    definitive = True
    for asset in assets_result:
        result = _fetch_and_materialize_release_asset(
            asset, source, binary_name, cache_dir, cfg, tag_result
        )
        if result.pages:
            return result
        definitive = definitive and result.definitive
    return _ProbeResult([], definitive)


def _release_tag(
    source: RepoSource,
    cache_dir: Path,
    cfg: Config,
    version: str | None,
    tag: str | None,
) -> str | _ProbeResult:
    """Validate the GitHub source and resolve its version-matched release tag."""
    if version is None or source.target.count("/") != 1:
        return _ProbeResult([], True)
    clone_url = source.clone_url
    if clone_url is None:
        return _ProbeResult([], False)
    resolved_tag = tag or _find_matching_tag_cached(cache_dir, clone_url, version, cfg)
    return resolved_tag if resolved_tag is not None else _ProbeResult([], True)


def _release_assets(
    source: RepoSource, cache_dir: Path, cfg: Config, tag: str
) -> list[object] | _ProbeResult:
    """Fetch and validate GitHub release metadata before inspecting assets."""
    metadata, definitive = _download_cached_result(
        f"https://api.github.com/repos/{source.target}/releases/tags/{tag}",
        cache_dir,
        cfg,
        max_age=_NEGATIVE_CACHE_TTL,
    )
    if metadata is None:
        return _ProbeResult([], definitive)
    try:
        document = json.loads(metadata)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _ProbeResult([], False)
    if not isinstance(document, dict):
        return _ProbeResult([], False)
    assets = document.get("assets", [])
    return assets if isinstance(assets, list) else _ProbeResult([], False)


def _fetch_and_materialize_release_asset(
    asset: object,
    source: RepoSource,
    binary_name: str,
    cache_dir: Path,
    cfg: Config,
    tag: str,
) -> _ProbeResult:
    """Validate one asset, fetch eligible bytes, then materialize matching pages."""
    if not isinstance(asset, dict):
        return _ProbeResult([], False)
    url = asset.get("browser_download_url")
    name = asset.get("name")
    if not isinstance(url, str) or not isinstance(name, str):
        return _ProbeResult([], False)
    direct = any(_matching_manpage_paths([name], binary_name))
    archive_candidate = _is_release_archive(name) and (
        _MAN_ASSET_TOKEN.search(name) is not None
        or (
            isinstance(asset.get("size"), int)
            and asset["size"] <= _RELEASE_ARCHIVE_CANDIDATE_LIMIT
        )
    )
    if not direct and not archive_candidate:
        return _ProbeResult([], True)
    content, definitive = _download_cached_result(url, cache_dir, cfg)
    if content is None:
        return _ProbeResult([], definitive)
    if direct:
        page = _materialize_page(cache_dir, source, tag, name, content)
        _record_page_uri(page, url)
        return (
            _ProbeResult([page], True)
            if _valid_page(page, binary_name)
            else _ProbeResult([], False)
        )
    try:
        pages = _manpages_from_release_archive(
            content, source, binary_name, cache_dir, tag, url
        )
    except (OSError, tarfile.TarError):
        return _ProbeResult([], False)
    return _ProbeResult(pages, True)


def _is_release_archive(name: str) -> bool:
    return name.lower().endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz"))


def _download_cached(url: str, cache_dir: Path, cfg: Config) -> bytes | None:
    return _download_cached_result(url, cache_dir, cfg)[0]


def _download_cached_result(
    url: str, cache_dir: Path, cfg: Config, max_age: int | None = None
) -> tuple[bytes | None, bool]:
    """Return cached bytes alongside whether absence is definitive.

    Release assets are immutable once named by a versioned URL.  GitHub's
    release metadata can gain assets after publication, so callers may give
    that response a short revalidation window.
    """
    destination = cache_dir / "releases" / sha256(url.encode()).hexdigest()
    negative_path = _upstream_cache_path(cache_dir, "downloads", url)
    freshness_path = _upstream_cache_path(cache_dir, "release-metadata", url)
    with _cache_lock(destination):
        try:
            if destination.stat().st_size <= _RELEASE_ARCHIVE_LIMIT:
                freshness = _read_json_cache(freshness_path)
                created = freshness.get("created") if freshness is not None else None
                if max_age is None or (
                    isinstance(created, (int, float))
                    and time.time() - created < max_age
                ):
                    return destination.read_bytes(), True
            else:
                destination.unlink()
        except OSError:
            pass
        negative = _read_json_cache(negative_path)
        created = negative.get("created") if negative is not None else None
        if (
            isinstance(created, (int, float))
            and time.time() - created < _NEGATIVE_CACHE_TTL
        ):
            return None, True
        _lookup_state.definitive = False
        content = _download(url, cfg)
        if content is None or len(content) > _RELEASE_ARCHIVE_LIMIT:
            if _lookup_state.definitive:
                _write_json_cache(negative_path, {"created": time.time()})
            return None, _lookup_state.definitive
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        temporary.replace(destination)
        if max_age is not None:
            _write_json_cache(freshness_path, {"created": time.time()})
        return content, True


def _manpages_from_release_archive(
    archive: bytes,
    source: RepoSource,
    binary_name: str,
    cache_dir: Path,
    tag: str,
    source_uri: str | None = None,
) -> list[Path]:
    """Materialize every valid page from an archive with a valid primary page."""
    pages: list[Path] = []
    primary: Path | None = None
    extracted_total = 0
    with tarfile.open(fileobj=BytesIO(archive), mode="r|*") as tar:
        for member in tar:
            if (
                not member.isfile()
                or not _safe_release_member_name(member.name)
                or not _is_manpage_filename(member.name)
                or not _is_related_bundle_page(member.name, binary_name)
                or member.size > _RELEASE_MEMBER_LIMIT
            ):
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            content = extracted.read(_RELEASE_MEMBER_LIMIT + 1)
            if len(content) > _RELEASE_MEMBER_LIMIT:
                continue
            extracted_total += len(content)
            if extracted_total > _RELEASE_EXTRACTED_LIMIT:
                return []
            page = _materialize_page(cache_dir, source, tag, member.name, content)
            _record_page_uri(page, source_uri)
            if not _is_valid_bundle_page(page):
                continue
            pages.append(page)
            if _matches_primary_manpage_name(member.name, binary_name) and _valid_page(
                page, binary_name
            ):
                primary = page
    if primary is None:
        return []
    return [primary, *(page for page in pages if page != primary)]


def _is_manpage_filename(path: str) -> bool:
    name = Path(path).name
    for suffix in (".gz", ".bz2", ".xz", ".zst"):
        if name.endswith(suffix):
            name = name.removesuffix(suffix)
            break
    return re.search(r"\.[1-9]$", name) is not None


def _matches_primary_manpage_name(path: str, binary_name: str) -> bool:
    name = Path(path).name
    patterns = [
        f"{binary_name}{variant}.[1-9]{suffix}"
        for variant in ("", "-*")
        for suffix in ("", ".gz", ".bz2", ".xz", ".zst")
    ]
    return any(fnmatch(name, pattern) for pattern in patterns)


def _is_related_bundle_page(path: str, binary_name: str) -> bool:
    name = Path(path).name
    stem = name
    for suffix in (".gz", ".bz2", ".xz", ".zst"):
        stem = stem.removesuffix(suffix)
    stem = stem.rsplit(".", 1)[0]
    return stem == binary_name or stem.startswith(
        (f"{binary_name}-", f"{binary_name}_")
    )


def _safe_release_member_name(path: str) -> bool:
    member_path = Path(path)
    return (
        not member_path.is_absolute()
        and ".." not in member_path.parts
        and len(member_path.name.encode()) <= 255
    )


def _is_valid_bundle_page(page: Path) -> bool:
    try:
        content = _read_bounded_manpage(page)
    except (EOFError, OSError, UnicodeError, lzma.LZMAError, zstandard.ZstdError):
        return False
    return (
        not is_help2man_content(content)
        and re.search(r"(?m)^\.(?:TH|Dt)\s+", content[:8192]) is not None
    )


def _read_bounded_manpage(page: Path) -> str:
    """Read at most one release member's decoded text, rejecting compression bombs."""
    limit = _RELEASE_MEMBER_LIMIT + 1
    if page.suffix == ".gz":
        opener = gzip.open
    elif page.suffix == ".bz2":
        opener = bz2.open
    elif page.suffix in {".xz", ".lzma"}:
        opener = lzma.open
    elif page.suffix == ".zst":
        stream = zstandard.ZstdDecompressor().stream_reader(page.open("rb"))
        with TextIOWrapper(stream, encoding="utf-8", errors="replace") as file:
            content = file.read(limit)
        if len(content) > _RELEASE_MEMBER_LIMIT:
            raise OSError("decompressed manpage exceeds limit")
        return content
    else:
        opener = open
    with opener(page, "rt", encoding="utf-8", errors="replace") as file:
        content = file.read(limit)
    if len(content) > _RELEASE_MEMBER_LIMIT:
        raise OSError("decompressed manpage exceeds limit")
    return content


def _download(url: str, cfg: Config) -> bytes | None:
    """Download bytes, recording whether a missing response was definitive."""
    content, definitive = _download_result(url, cfg)
    _lookup_state.definitive = definitive
    return content


def _download_result(url: str, cfg: Config) -> tuple[bytes | None, bool]:
    try:
        with urlopen(
            Request(url, headers={"User-Agent": "maniac"}), timeout=cfg.timeout_git
        ) as response:
            content = response.read(_RELEASE_ARCHIVE_LIMIT + 1)
            if len(content) > _RELEASE_ARCHIVE_LIMIT:
                return None, False
            return content, True
    except HTTPError as error:
        logger.debug("Download failed", url=url, error=str(error))
        return None, error.code in {404, 410}
    except (OSError, URLError, ValueError) as error:
        logger.debug("Download failed", url=url, error=str(error))
        return None, False


def _valid_page(page: Path, binary_name: str) -> bool:
    try:
        content = _read_bounded_manpage(page)
    except (EOFError, OSError, UnicodeError, lzma.LZMAError, zstandard.ZstdError):
        return False
    return not is_help2man_content(content) and manpage_documents(content, binary_name)


def _tag_candidates(version: str) -> tuple[str, str]:
    """Return the conventional tag spellings for an installed version."""
    return (f"v{version}", version)


def _cache_matches_source(cache_dir: Path, clone_url: str, timeout: int) -> bool:
    """Whether a cached checkout still belongs to the resolved repository."""
    if not (cache_dir / ".git").exists():
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(cache_dir), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == clone_url


def _upstream_cache_path(cache_dir: Path, kind: str, *parts: str) -> Path:
    digest = sha256("\0".join(parts).encode()).hexdigest()
    return cache_dir / "upstream" / kind / f"{digest}.json"


@contextmanager
def _cache_lock(path: Path) -> Iterator[None]:
    """Serialize a cache key across threads and processes."""
    with _cache_locks_guard:
        thread_lock = _cache_locks.setdefault(path, threading.Lock())
    with thread_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(".lock")
        with lock_path.open("w", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield


def _read_json_cache(path: Path) -> dict[str, object] | None:
    try:
        if path.stat().st_size > _CACHE_MAX_BYTES:
            raise OSError("cache entry exceeds limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return None
    if isinstance(payload, dict):
        return payload
    path.unlink(missing_ok=True)
    return None


def _write_json_cache(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, encoding="utf-8", delete=False
        ) as file:
            temporary = Path(file.name)
            json.dump(payload, file, separators=(",", ":"))
        temporary.replace(path)
    except OSError as error:
        logger.debug("Could not write upstream cache", path=str(path), error=str(error))


def _find_matching_tag_cached(
    cache_dir: Path, clone_url: str, version: str, cfg: Config
) -> str | None:
    return _find_matching_tag_cached_result(cache_dir, clone_url, version, cfg)[0]


def _find_matching_tag_cached_result(
    cache_dir: Path, clone_url: str, version: str, cfg: Config
) -> tuple[str | None, bool]:
    """Return a matching tag alongside whether absence is definitive."""
    path = _upstream_cache_path(cache_dir, "tags", clone_url, version)
    with _cache_lock(path):
        cached = _read_json_cache(path)
        if cached is not None:
            tag = cached.get("tag")
            if isinstance(tag, str) and tag:
                return tag, True
            created = cached.get("created")
            if (
                tag is None
                and isinstance(created, (int, float))
                and time.time() - created < _NEGATIVE_CACHE_TTL
            ):
                return None, True
        _lookup_state.definitive = False
        tag = _find_matching_tag(clone_url, version, cfg.timeout_git)
        if tag is not None or _lookup_state.definitive:
            _write_json_cache(path, {"tag": tag, "created": time.time()})
        return tag, tag is not None or _lookup_state.definitive


def _read_probe_cache(
    path: Path, cache_dir: Path, binary_name: str
) -> list[Path] | None:
    cached = _read_json_cache(path)
    if cached is None:
        return None
    pages = cached.get("pages")
    if isinstance(pages, list) and all(isinstance(page, str) for page in pages):
        resolved = [Path(page) for page in pages]
        manpages_dir = cache_dir / "manpages"
        if (
            resolved
            and all(
                page.is_relative_to(manpages_dir) and page.exists() for page in resolved
            )
            and _valid_page(resolved[0], binary_name)
            and all(_is_valid_bundle_page(page) for page in resolved[1:])
        ):
            return resolved
    created = cached.get("created")
    if (
        pages == []
        and isinstance(created, (int, float))
        and time.time() - created < _NEGATIVE_CACHE_TTL
    ):
        return []
    path.unlink(missing_ok=True)
    return None


def _write_probe_cache(path: Path, pages: list[Path]) -> None:
    _write_json_cache(
        path, {"pages": [str(page) for page in pages], "created": time.time()}
    )


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
        _lookup_state.definitive = False
        return None
    if result.returncode != 0:
        _lookup_state.definitive = False
        return None

    tags: set[str] = set()
    for line in result.stdout.splitlines():
        _, _, ref = line.partition("refs/tags/")
        if ref:
            tags.add(ref.removesuffix("^{}"))

    for candidate in _tag_candidates(version):
        if candidate in tags:
            _lookup_state.definitive = True
            return candidate
    _lookup_state.definitive = True
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
    # Synthesis only needs root documentation initially.  Sparse clone keeps
    # hostile fixture paths out of the filesystem entirely.
    cmd = ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse"]
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
        sparse = subprocess.run(
            [
                "git",
                "-C",
                str(dest_dir),
                "sparse-checkout",
                "set",
                "--no-cone",
                *sorted(DOC_DIRS),
            ],
            capture_output=True,
            text=True,
            timeout=cfg.timeout_git,
            check=False,
        )
        if sparse.returncode == 0:
            return True
        log = logger.debug if optional else logger.error
        log("Failed configuring sparse checkout", url=clone_url)
        shutil.rmtree(dest_dir, ignore_errors=True)
        return False

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
