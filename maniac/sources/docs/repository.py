"""Repository acquisition: bare-ref fetching, tag selection, versioned caches."""

import fcntl
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from ...config import Config
from ...logging import logger
from ...models import DocFile, RepoSource
from . import cache, extraction, pages
from .cache import _NEGATIVE_CACHE_TTL, _lookup_state
from .extraction import DOC_DIRS
from .pages import _Probe, _ProbeResult


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


def _find_matching_tag_cached(
    cache_dir: Path, clone_url: str, version: str, cfg: Config
) -> str | None:
    return _find_matching_tag_cached_result(cache_dir, clone_url, version, cfg)[0]


def _find_matching_tag_cached_result(
    cache_dir: Path, clone_url: str, version: str, cfg: Config
) -> tuple[str | None, bool]:
    """Return a matching tag alongside whether absence is definitive."""
    path = cache._upstream_cache_path(cache_dir, "tags", clone_url, version)
    with cache._cache_lock(path):
        cached = cache._read_json_cache(path)
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
            cache._write_json_cache(path, {"tag": tag, "created": time.time()})
        return tag, tag is not None or _lookup_state.definitive


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


def _discover_remote_manpage_result(probe: _Probe) -> _ProbeResult:
    """Probe a repository tree and retain whether it was completely inspected."""
    clone_url = probe.source.clone_url
    if clone_url is None:
        return _ProbeResult([], False)
    ref_name = "default"
    fetch_ref = "HEAD"
    source_ref = "HEAD"
    if probe.version is not None:
        tag = probe.tag or _find_matching_tag_cached(
            probe.cache_dir, clone_url, probe.version, probe.cfg
        )
        if tag is None:
            return _ProbeResult([], True)
        ref_name = tag
        fetch_ref = f"refs/tags/{tag}"
        source_ref = tag
    repo = _bare_cache_dir(probe.cache_dir, clone_url)
    ref = _fetch_bare_ref(repo, clone_url, ref_name, fetch_ref, probe.cfg)
    if ref is None:
        return _ProbeResult([], False)
    paths = _git_stdout(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", ref], probe.cfg
    )
    if paths is None:
        return _ProbeResult([], False)
    definitive = True
    for path in pages._matching_manpage_paths(paths.splitlines(), probe.binary_name):
        content = _git_bytes(
            ["git", "-C", str(repo), "show", f"{ref}:{path}"], probe.cfg
        )
        if content is None:
            definitive = False
            continue
        cached = pages._materialize_page(
            probe.cache_dir, probe.source, ref_name, path, content
        )
        pages._record_page_uri(
            cached, pages._repository_page_uri(probe.source, source_ref, path)
        )
        if pages._valid_page(cached, probe.binary_name):
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
        for doc in extraction.extract_docs_from_dir(
            wiki_dir, max_total_chars=max_total_chars
        )
    ]
