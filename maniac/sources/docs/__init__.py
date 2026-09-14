"""Repository fetching and documentation extraction.

One facade over repository acquisition (`repository`), GitHub release
retrieval (`release`), page selection (`pages`), cache primitives (`cache`),
and documentation extraction (`extraction`).
"""

from dataclasses import replace
from pathlib import Path

from ...config import Config
from ...models import DocFile, LocalRepoSource, RepoSource
from ..manpages import find_repo_manpage
from . import cache, extraction, pages, release, repository
from .extraction import MAX_TOTAL_DOC_CHARS
from .pages import _Probe, _ProbeResult

__all__ = [
    "discover_repo_manpage",
    "discover_repo_manpages",
    "fetch_and_extract_docs",
]


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
    same path ADR-0016 tier 2 uses); the wiki
    (`repository._fetch_github_wiki_docs`) stays in the context either way,
    since it carries no version of its own to disagree with. Falling back to
    the default branch on no match, rather than returning no docs, is what
    tier 2 refuses and tier 3 -- the last resort -- does not: the page is
    still worth having, only the version claim is not.
    """
    cfg = config or Config()
    cache_dir_path = Path(cache_dir) if cache_dir is not None else cfg.cache_dir
    target_path = repository.resolve_repo_dir(
        source, cache_dir_path, cfg, version=version
    )
    matched = version is not None and target_path is not None
    if target_path is None and version is not None:
        target_path = repository.resolve_repo_dir(source, cache_dir_path, cfg)
    if target_path is None:
        return [], False

    doc_files = extraction.extract_docs_from_dir(
        target_path, max_total_chars=max_total_chars
    )
    if isinstance(source, LocalRepoSource):
        return doc_files, matched

    remaining_chars = max_total_chars - sum(len(doc.content) for doc in doc_files)
    if remaining_chars <= 0:
        return doc_files, matched

    return (
        doc_files
        + repository._fetch_github_wiki_docs(
            source, cache_dir_path, cfg, remaining_chars
        ),
        matched,
    )


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
    found = discover_repo_manpages(
        source,
        binary_name,
        cache_dir=cache_dir,
        config=config,
        version=version,
    )
    return found[0] if found else None


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
    if isinstance(source, LocalRepoSource):
        page = find_repo_manpage(source.path, binary_name)
        return (
            [page] if page is not None and pages._valid_page(page, binary_name) else []
        )

    clone_url = source.clone_url
    if clone_url is None:
        return []

    probe = _Probe(source, binary_name, cache_dir_path, cfg, version)
    # Versioned upstream results are immutable once published.  Holding this
    # lock across a cold probe makes concurrent list rows share one network trip.
    if version is None:
        return _discover_remote_then_release(probe).pages
    probe_path = cache._upstream_cache_path(
        cache_dir_path, "probes", "source-uri-v1", clone_url, version, binary_name
    )
    with cache._cache_lock(probe_path):
        cached = pages._read_probe_cache(probe_path, cache_dir_path, binary_name)
        if cached is not None:
            return cached
        tag, tag_definitive = repository._find_matching_tag_cached_result(
            cache_dir_path, clone_url, version, cfg
        )
        result = _discover_remote_then_release(replace(probe, tag=tag))
        if result.pages or (tag_definitive and result.definitive):
            pages._write_probe_cache(probe_path, result.pages)
        return result.pages


def _discover_remote_then_release(probe: _Probe) -> _ProbeResult:
    if probe.version is not None and probe.tag is None:
        return _ProbeResult([], True)
    remote = repository._discover_remote_manpage_result(probe)
    if remote.pages:
        return remote
    found = release._discover_github_release_manpages_result(probe)
    return _ProbeResult(found.pages, remote.definitive and found.definitive)
