"""Mise-installed binaries under `~/.local/share/mise/installs/` (ADR-0015)."""

import tomllib
from pathlib import Path

from ...config import Config
from ...logging import logger
from ...models import Installation, RepoSource
from .. import discovery
from ..manpages import find_install_root_manpages
from ..pathcache import resolve_cached
from .base import Provider
from .registry import registry

_INSTALLS_MARKER = "/.local/share/mise/installs/"
_DIRECT_BACKENDS = ("aqua", "github")

# Backend name -> the delegate provider's install root, relative to mise's
# own install root, for a backend whose on-disk layout mise composes rather
# than reinvents. Confirmed against real installs on the development system:
# an npm-backend install's package.json lives under `node_modules/<pkg>/`
# (mise's own wrapper package sits at the root itself), and a pipx-backend
# install nests its venv one level down, named after the package.
_COMPOSED_BACKENDS = {
    "npm": lambda root, package: root / "node_modules" / package,
    "pipx": lambda root, package: root / package,
}


class MiseProvider:
    """Detects a mise install; resolves it from `.mise.backend.toml` first,
    falling back to the mise registry keyed on the install directory name
    (ADR-0015 Stage 3).
    """

    name = "mise"

    def can_detect(self, real_path: Path) -> bool:
        """Recognise mise's versioned installation layout."""
        return _INSTALLS_MARKER in str(real_path)

    def detect(self, bin_path: Path) -> Installation | None:
        resolved = resolve_cached(bin_path)
        if _INSTALLS_MARKER not in str(resolved):
            return None
        parts = resolved.parts
        idx = parts.index("installs")
        if idx + 3 >= len(parts):
            # Need tool_id, version, and at least one segment below it (the
            # binary itself at minimum) -- otherwise "version" would be the
            # binary's own filename and root would equal the binary's path.
            return None
        tool_id, version = parts[idx + 1], parts[idx + 2]
        root = Path(*parts[: idx + 3])
        return Installation(
            binary=bin_path.name,
            bin_path=bin_path,
            real_path=resolved,
            provider=self.name,
            package=tool_id,
            version=version,
            root=root,
            parent=_build_parent(bin_path, resolved, root),
        )

    def resolve_source(
        self, inst: Installation, *, config: Config, offline: bool = False
    ) -> RepoSource | None:
        """`offline`, used by `maniac list` (ADR-0018), skips only the mise-registry
        network fallback inside `discovery._resolve_from_mise` -- the parent
        delegation and `.mise.backend.toml` read below are pure filesystem
        already, so neither branch needs it.
        """
        if inst.parent is not None:
            provider = _find_provider(inst.parent.provider)
            return (
                provider.resolve_source(inst.parent, config=config)
                if provider
                else None
            )
        backend_record = _read_backend_record(inst.root)
        if backend_record is not None:
            repo = _repo_from_backend(*backend_record)
            return (
                RepoSource(name=inst.binary, target=repo, is_local=False)
                if repo
                else None
            )
        repo = discovery._resolve_from_mise(
            inst.package, inst.binary, config=config, offline=offline
        )
        return (
            RepoSource(name=inst.binary, target=repo, is_local=False) if repo else None
        )

    def local_docs(self, inst: Installation) -> list[Path]:
        return find_install_root_manpages(inst.root, inst.binary)


def _read_backend_record(root: Path) -> tuple[str, str] | None:
    """Read `full = "backend:package"` from `.mise.backend.toml`, if present.

    The file lives one level above the version-specific install `root`,
    shared across every version mise has installed for the tool.
    """
    backend_path = root.parent / ".mise.backend.toml"
    try:
        data = tomllib.loads(backend_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        logger.debug(
            "Error parsing mise backend record", path=str(backend_path), error=str(e)
        )
        return None
    full = data.get("full")
    if not isinstance(full, str) or ":" not in full:
        return None
    backend, _, package = full.partition(":")
    return backend, package


def _repo_from_backend(backend: str, package: str) -> str | None:
    """Return "owner/repo" for a backend whose package identity already is one.

    Aqua and GitHub both name a GitHub repository directly, matching
    `RepoSource.clone_url`'s existing aqua-package handling -- neither needs
    a delegate provider, unlike npm/pipx/cargo/go, which compose through
    `Installation.parent` instead (`_build_parent`). Every other backend
    (mise's own "core", ...) has no provider to delegate to and stays
    unresolved rather than guessed at.
    """
    if backend not in _DIRECT_BACKENDS:
        return None
    segments = package.split("/")
    if len(segments) < 2:
        return None
    return "/".join(segments[:2])


def _build_parent(bin_path: Path, real_path: Path, root: Path) -> Installation | None:
    """Build the backend's own view of this install, if `.mise.backend.toml`
    names a backend this codebase has a provider for and a known on-disk
    shape to compose through -- `_COMPOSED_BACKENDS`.
    """
    backend_record = _read_backend_record(root)
    if backend_record is None:
        return None
    backend, package = backend_record
    build_root = _COMPOSED_BACKENDS.get(backend)
    if build_root is None:
        return None
    return Installation(
        binary=bin_path.name,
        bin_path=bin_path,
        real_path=real_path,
        provider=backend,
        package=package,
        version=None,
        root=build_root(root, package),
    )


def _find_provider(name: str) -> Provider | None:
    for provider in registry:
        if provider.name == name:
            return provider
    return None
