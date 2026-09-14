"""Homebrew installs, `<prefix>/bin/foo -> ../Cellar/foo/1.2.3/...` (ADR-0015 Stage 4).

Unverified: Homebrew is not installed on the development system (see
`docs/BACKLOG.md`). Written against Homebrew's documented Cellar layout and
`brew info --json` schema.
"""

import json
import subprocess
from pathlib import Path

from ...config import Config
from ...logging import logger
from ...models import Installation, RemoteRepoSource, RepoSource
from .. import discovery
from ..manpages import find_install_root_manpages
from ..pathcache import resolve_cached
from .base import SourceResolver

_CELLAR_MARKER = "/Cellar/"


class HomebrewProvider:
    """Detects a Homebrew-installed binary from its `Cellar` path -- the same
    symlink shape mise uses -- and resolves it through `brew info --json`,
    the one place Homebrew records a package's homepage.
    """

    name = "homebrew"

    def can_detect(self, real_path: Path) -> bool:
        """Recognise Homebrew's Cellar layout without calling brew."""
        return _CELLAR_MARKER in str(real_path)

    def detect(self, bin_path: Path) -> Installation | None:
        resolved = resolve_cached(bin_path)
        resolved_str = str(resolved)
        if _CELLAR_MARKER not in resolved_str:
            return None
        prefix, _, tail = resolved_str.partition(_CELLAR_MARKER)
        segments = tail.split("/")
        if len(segments) < 2 or not segments[0] or not segments[1]:
            return None
        package, version = segments[0], segments[1]
        root = Path(prefix + _CELLAR_MARKER + package + "/" + version)
        return Installation(
            binary=bin_path.name,
            bin_path=bin_path,
            real_path=resolved,
            provider=self.name,
            package=package,
            version=version,
            root=root,
        )

    def resolve_source(
        self, inst: Installation, *, config: Config, sources: SourceResolver
    ) -> RepoSource | None:
        homepage = _brew_homepage(inst.package)
        if not homepage:
            return None
        cleaned = discovery._clean_git_url(homepage)
        if cleaned == homepage:
            return None
        return RemoteRepoSource.from_identifier(inst.binary, cleaned)

    def local_docs(self, inst: Installation) -> list[Path]:
        return find_install_root_manpages(inst.root, inst.binary)


def _brew_homepage(package: str) -> str | None:
    try:
        result = subprocess.run(
            ["brew", "info", "--json=v2", package],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("Error running brew info", package=package, error=str(e))
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        logger.debug("Error parsing brew info JSON", package=package, error=str(e))
        return None
    formulae = data.get("formulae") if isinstance(data, dict) else None
    if not formulae:
        return None
    homepage = formulae[0].get("homepage")
    return homepage if isinstance(homepage, str) else None
