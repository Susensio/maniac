"""A tool's completion from its repository, for exactly the installed version.

The upstream source for completions (ADR-0066), as `discover_repo_manpages`
is for pages, and with the same rule: only the tag matching the installed
version is asked (ADR-0016), so a completion never offers flags another
version had. The repository tree at that tag is searched (eza keeps
`completions/zsh/_eza`, fd `contrib/completion/_fd`), then the GitHub
release's assets named as a completion file. Release archives are not
unpacked: the platform tarballs that carry completions are megabytes each,
and a tool shipping one usually has a generator too.

The answer carries whether the check completed (CONTRACT.md rule 4): a
network or git failure is not a definitive "none".
"""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ...config import Config
from ...models import LocalRepoSource, RepoSource
from ..completions import file_names, looks_like_completion
from . import cache, pages, release, repository

_SKIPPED_PARTS = {
    "test",
    "tests",
    "testdata",
    "fixtures",
    "vendor",
    "node_modules",
    "third_party",
    "examples",
}
_MAX_DEPTH = 5
_COMPLETION_DIRS = {
    "completions",
    "completion",
    "complete",
    "autocomplete",
    "contrib",
    "site-functions",
    "vendor_completions.d",
    "bash-completion",
    "bash_completion.d",
}


@dataclass(frozen=True, slots=True)
class Upstream:
    """A completion script from the repository, and where it was read."""

    script: str
    uri: str | None


def upstream_completion(
    source: RepoSource,
    tool: str,
    shell: str,
    *,
    version: str | None,
    cache_dir: Path,
    config: Config,
) -> tuple[Upstream | None, bool]:
    """`tool`'s completion for `shell` at the tag matching `version`, and
    whether the check was definitive."""
    if version is None or isinstance(source, LocalRepoSource):
        return None, True
    clone_url = source.clone_url
    if clone_url is None:
        return None, True
    tag, tag_definitive = repository._find_matching_tag_cached_result(
        cache_dir, clone_url, version, config
    )
    if tag is None:
        return None, tag_definitive
    found, definitive = _from_tree(
        source, clone_url, tag, tool, shell, cache_dir, config
    )
    if found is not None:
        return found, True
    if source.identity.count("/") == 1:
        found, assets_definitive = _from_release_assets(
            source, tag, tool, shell, version, cache_dir, config
        )
        if found is not None:
            return found, True
        definitive = definitive and assets_definitive
    return None, definitive


def _from_tree(
    source: RepoSource,
    clone_url: str,
    tag: str,
    tool: str,
    shell: str,
    cache_dir: Path,
    config: Config,
) -> tuple[Upstream | None, bool]:
    repo = repository._bare_cache_dir(cache_dir, clone_url)
    ref = repository._fetch_bare_ref(repo, clone_url, tag, f"refs/tags/{tag}", config)
    if ref is None:
        return None, False
    listing = repository._git_stdout(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", ref], config
    )
    if listing is None:
        return None, False
    definitive = True
    for path in candidate_paths(listing.splitlines(), tool, shell):
        content = repository._git_bytes(
            ["git", "-C", str(repo), "show", f"{ref}:{path}"], config
        )
        if content is None:
            definitive = False
            continue
        script = content.decode("utf-8", errors="replace")
        if looks_like_completion(script, shell, tool):
            uri = pages._repository_page_uri(source, tag, path)
            return Upstream(script, uri), True
    return None, definitive


def candidate_paths(paths: list[str], tool: str, shell: str) -> list[str]:
    """Paths in a repository tree that may hold `tool`'s completion for
    `shell`, most likely first: in a completion directory, then shallowest."""
    names, bare = file_names(shell, tool)
    found = []
    for path in paths:
        parts = PurePosixPath(path).parts
        lowered = [part.lower() for part in parts[:-1]]
        if len(parts) > _MAX_DEPTH or _SKIPPED_PARTS.intersection(lowered):
            continue
        name = parts[-1]
        if name in names or (
            bare is not None and name == bare and any(shell in p for p in lowered)
        ):
            in_completion_dir = bool(_COMPLETION_DIRS.intersection(lowered))
            found.append((not in_completion_dir, len(parts), path))
    return [path for _, _, path in sorted(found)]


def _from_release_assets(
    source: RepoSource,
    tag: str,
    tool: str,
    shell: str,
    version: str,
    cache_dir: Path,
    config: Config,
) -> tuple[Upstream | None, bool]:
    probe = pages._Probe(source, tool, cache_dir, config, version, tag)
    assets = release._release_assets(probe, tag)
    if isinstance(assets, pages._ProbeResult):
        return None, assets.definitive
    names, _ = file_names(shell, tool)
    definitive = True
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("name") not in names:
            continue
        url = asset.get("browser_download_url")
        if not isinstance(url, str):
            continue
        content, fetched = cache._download_cached_result(url, cache_dir, config)
        if content is None:
            definitive = definitive and fetched
            continue
        script = content.decode("utf-8", errors="replace")
        if looks_like_completion(script, shell, tool):
            return Upstream(script, url), True
    return None, definitive
