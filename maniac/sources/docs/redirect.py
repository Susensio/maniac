"""A recorded repository that points at the tool's own (ADR-0067).

An installer can record a repository that packages a tool rather than the
one that writes it: pyright from PyPI records a Python wrapper, tmux from
mise a repository of static builds. `detect` reads what that repository
says about itself -- its README, build scripts and CI workflows -- for a
GitHub repository named exactly like the binary or its package, and
answers it only when that repository has a tag for the installed version.
"""

import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ...config import Config
from ...models import RepoSource
from . import cache, repository

_MAX_FILES = 40
_MAX_BYTES = 200_000
_LINK = re.compile(
    r"github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?![A-Za-z0-9_.-])"
)
_BUILD_FILES = re.compile(
    r"(?i)^(readme[^/]*|[^/]*\.sh|makefile|dockerfile[^/]*|"
    r"scripts/[^/]+|\.github/workflows/[^/]+\.ya?ml)$"
)


@dataclass(frozen=True, slots=True)
class Redirect:
    """The tool's own repository, as the recorded one names it."""

    target: str
    """`owner/repo`."""
    links: int
    """How many times the recorded repository links it."""
    files: tuple[str, ...]
    """Where: the recorded repository's files that link it."""
    tag: str
    """Its tag for the installed version."""

    def evidence(self, source: RepoSource) -> str:
        where = ", ".join(self.files[:3])
        times = "once" if self.links == 1 else f"{self.links} times"
        return (
            f"{source.identity} links {self.target} {times} ({where}), "
            f"which has the tag {self.tag}"
        )


def is_asked(source: RepoSource, binary: str, package: str) -> bool:
    """Whether `source` is a GitHub repository not named like its tool: only
    then is it searched for another."""
    if source.clone_url is None or not source.clone_url.startswith(
        "https://github.com/"
    ):
        return False
    if source.identity.count("/") != 1:
        return False
    return _named(source.identity.split("/")[1]) not in {
        _named(binary),
        _named(package),
    }


def _named(name: str) -> str:
    """A name as packages spell it either way: `ripgrep_all` is `ripgrep-all`."""
    return re.sub(r"[-_.]+", "-", name.lower())


def _enough(links: int, files: list[str]) -> bool:
    """Whether the links are evidence rather than a mention: two of them, or
    one from a build script or workflow, which builds or fetches the target.
    A single README line can be an "inspired by"."""
    built = any(not PurePosixPath(f).name.lower().startswith("readme") for f in files)
    return links >= 2 or built


def detect(
    source: RepoSource,
    *,
    binary: str,
    package: str,
    version: str | None,
    cache_dir: Path,
    config: Config,
) -> tuple[Redirect | None, bool]:
    """The repository `source` points at as the tool's own, and whether the
    answer is final; a check that did not finish is asked again (rule 4)."""
    if version is None or not is_asked(source, binary, package):
        return None, True
    path = cache._upstream_cache_path(
        cache_dir, "redirects", source.identity, binary, package, version
    )
    with cache._cache_lock(path):
        cached = cache._read_json_cache(path)
        if cached is not None and "redirect" in cached:
            return _from_json(cached["redirect"]), True
        redirect, definitive = _detect(
            source, binary, package, version, cache_dir, config
        )
        if definitive:
            cache._write_json_cache(
                path, {"redirect": _to_json(redirect), "created": time.time()}
            )
        return redirect, definitive


def _detect(
    source: RepoSource,
    binary: str,
    package: str,
    version: str,
    cache_dir: Path,
    config: Config,
) -> tuple[Redirect | None, bool]:
    clone_url = source.clone_url
    assert clone_url is not None
    tag, definitive = repository._find_matching_tag_cached_result(
        cache_dir, clone_url, version, config
    )
    if tag is None and not definitive:
        return None, False
    ref_name, fetch_ref = (tag, f"refs/tags/{tag}") if tag else ("default", "HEAD")
    repo = repository._bare_cache_dir(cache_dir, clone_url)
    ref = repository._fetch_bare_ref(repo, clone_url, ref_name, fetch_ref, config)
    if ref is None:
        return None, False
    # Names only: `-l` would make a blobless fetch download every file to
    # learn its size.
    listing = repository._git_stdout(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", ref], config
    )
    if listing is None:
        return None, False
    links: Counter[str] = Counter()
    files: dict[str, list[str]] = {}
    names = {_named(binary), _named(package)}
    own = source.identity.lower()
    for path in _build_files(listing)[:_MAX_FILES]:
        content = repository._git_bytes(
            ["git", "-C", str(repo), "show", f"{ref}:{path}"], config
        )
        if content is None:
            return None, False
        if len(content) > _MAX_BYTES:
            continue
        for owner, name in _LINK.findall(content.decode("utf-8", errors="replace")):
            target = f"{owner}/{name}"
            if _named(name) in names and target.lower() != own:
                links[target] += 1
                files.setdefault(target, []).append(path)
    if not links:
        return None, True
    target, count = links.most_common(1)[0]
    if not _enough(count, files[target]):
        return None, True
    target_tag, target_definitive = repository._find_matching_tag_cached_result(
        cache_dir, f"https://github.com/{target}.git", version, config
    )
    if target_tag is None:
        return None, target_definitive
    return Redirect(
        target, count, tuple(dict.fromkeys(files[target])), target_tag
    ), True


def _build_files(listing: str) -> list[str]:
    """The README, build scripts and CI workflows in a tree listing, README first:
    it is where a wrapper says what it wraps."""
    found = [path for path in listing.splitlines() if _BUILD_FILES.match(path)]
    return sorted(
        found,
        key=lambda p: (not PurePosixPath(p).name.lower().startswith("readme"), p),
    )


def _to_json(redirect: Redirect | None) -> dict[str, object] | None:
    if redirect is None:
        return None
    return {
        "target": redirect.target,
        "links": redirect.links,
        "files": list(redirect.files),
        "tag": redirect.tag,
    }


def _from_json(raw: object) -> Redirect | None:
    if not isinstance(raw, dict):
        return None
    try:
        return Redirect(
            str(raw["target"]), int(raw["links"]), tuple(raw["files"]), str(raw["tag"])
        )
    except (KeyError, TypeError, ValueError):
        return None
