"""Mise-installed binaries under `~/.local/share/mise/installs/` (ADR-0015)."""

import tomllib
from pathlib import Path

from ...logging import logger
from ...models import Installation, RepoSource
from .. import discovery

_INSTALLS_MARKER = "/.local/share/mise/installs/"
_DIRECT_BACKENDS = ("aqua", "github")


class MiseProvider:
    """Detects a mise install; resolves it from `.mise.backend.toml` first,
    falling back to the mise registry keyed on the install directory name
    (ADR-0015 Stage 3).
    """

    name = "mise"

    def detect(self, bin_path: Path) -> Installation | None:
        resolved = bin_path.resolve()
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
        return Installation(
            binary=bin_path.name,
            bin_path=bin_path,
            real_path=resolved,
            provider=self.name,
            package=tool_id,
            version=version,
            root=Path(*parts[: idx + 3]),
        )

    def resolve_source(self, inst: Installation) -> RepoSource | None:
        backend_record = _read_backend_record(inst.root)
        if backend_record is not None:
            repo = _repo_from_backend(*backend_record)
            return (
                RepoSource(name=inst.binary, target=repo, is_local=False)
                if repo
                else None
            )
        repo = discovery._resolve_from_mise(inst.package, inst.binary)
        return (
            RepoSource(name=inst.binary, target=repo, is_local=False) if repo else None
        )

    def local_docs(self, inst: Installation) -> list[Path]:
        # Wiring this to the install root is Stage 5's job.
        return []


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
    `RepoSource.clone_url`'s existing aqua-package handling. Every other
    backend (npm, pipx, cargo, mise's own "core", ...) needs its own
    provider to turn a package identity into a repository -- Stage 4's job,
    not this one's to guess at.
    """
    if backend not in _DIRECT_BACKENDS:
        return None
    segments = package.split("/")
    if len(segments) < 2:
        return None
    return "/".join(segments[:2])
