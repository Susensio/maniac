"""Mise-installed binaries under `~/.local/share/mise/installs/` (ADR-0015)."""

from pathlib import Path

from ...models import Installation, RepoSource
from .. import discovery

_INSTALLS_MARKER = "/.local/share/mise/installs/"


class MiseProvider:
    """Detects a mise install; identity resolution stays `discovery`'s existing
    mise-registry lookup, prefix parsing included -- Stage 3 replaces that outright.
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
        # `_resolve_from_mise`'s `allow_binary_registry_match` has no home on the
        # Provider protocol yet; this uses its permissive default. The restrictive
        # path `discover_candidate_source` needs is threaded directly in
        # `discovery._resolve_symlink_target` instead of through this method.
        repo = discovery._resolve_from_mise(inst.package, inst.binary)
        return (
            RepoSource(name=inst.binary, target=repo, is_local=False) if repo else None
        )

    def local_docs(self, inst: Installation) -> list[Path]:
        # Wiring this to the install root is Stage 5's job.
        return []
