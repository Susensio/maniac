"""npm global installs under `<prefix>/lib/node_modules/<pkg>/` (ADR-0015 Stage 4)."""

import json
from pathlib import Path

from ...config import Config
from ...exceptions import MalformedToolMetadata
from ...models import Installation, RemoteRepoSource, RepoSource
from .. import discovery
from ..manpages import find_install_root_manpages
from ..pathcache import resolve_cached
from .base import SourceResolver

_NODE_MODULES_MARKER = "/lib/node_modules/"


class NpmProvider:
    """Detects an `npm install -g` package; resolves it from `package.json`'s
    own `repository` field -- an explicit field, never inferred from the
    package name.
    """

    name = "npm"

    def can_detect(self, real_path: Path) -> bool:
        """Recognise npm's global node_modules layout."""
        return _NODE_MODULES_MARKER in str(real_path)

    def detect(self, bin_path: Path) -> Installation | None:
        resolved = resolve_cached(bin_path)
        resolved_str = str(resolved)
        if _NODE_MODULES_MARKER not in resolved_str:
            return None
        prefix, _, tail = resolved_str.partition(_NODE_MODULES_MARKER)
        segments = tail.split("/")
        if not segments or not segments[0]:
            return None
        # A scoped package ("@scope/name") is two path segments, not one.
        if segments[0].startswith("@") and len(segments) >= 2 and segments[1]:
            package = f"{segments[0]}/{segments[1]}"
        else:
            package = segments[0]
        root = Path(prefix + _NODE_MODULES_MARKER + package)
        return Installation(
            binary=bin_path.name,
            bin_path=bin_path,
            real_path=resolved,
            provider=self.name,
            package=package,
            version=read_package_json(root).get("version"),
            root=root,
        )

    def resolve_source(
        self, inst: Installation, *, config: Config, sources: SourceResolver
    ) -> RepoSource | None:
        repo = _repo_from_repository_field(
            read_package_json(inst.root).get("repository")
        )
        return RemoteRepoSource.from_identifier(inst.binary, repo) if repo else None

    def local_docs(self, inst: Installation) -> list[Path]:
        return find_install_root_manpages(inst.root, inst.binary)


def read_package_json(root: Path) -> dict:
    path = root / "package.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise MalformedToolMetadata(path, str(e)) from e
    if not isinstance(data, dict):
        raise MalformedToolMetadata(path, "package.json is not a JSON object")
    return data


def _repo_from_repository_field(value: object) -> str | None:
    """Read `package.json`'s `repository` field as npm itself defines it:

    a string ("github:owner/repo", a full git URL, or bare "owner/repo"
    shorthand) or `{"type": "git", "url": "..."}`. No inference beyond
    what the field states.
    """
    if isinstance(value, dict):
        url = value.get("url")
    elif isinstance(value, str):
        url = value
    else:
        return None
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("github:"):
        return url.removeprefix("github:")
    cleaned = discovery._clean_git_url(url)
    if cleaned != url:
        return cleaned
    if "/" in url and url.count("/") == 1 and "://" not in url:
        return url
    return None
