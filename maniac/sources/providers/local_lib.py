"""`~/.local/lib/<tool>` checkouts, resolved to a git remote when one exists (ADR-0015)."""

import subprocess
from pathlib import Path

from ...logging import logger
from ...models import Installation, RepoSource
from .. import discovery
from ..manpages import find_install_root_manpages
from ..pathcache import resolve_cached

_LIB_MARKER = "/.local/lib/"


class LocalLibProvider:
    """Detects a `~/.local/lib/<tool>` checkout; a raw directory tree, not a
    package manager's install, so identity is the directory name and there is
    no version to report.
    """

    name = "local_lib"

    def detect(self, bin_path: Path) -> Installation | None:
        resolved = resolve_cached(bin_path)
        if _LIB_MARKER not in str(resolved):
            return None
        base_lib = Path.home() / ".local" / "lib"
        tool_dir = resolved
        if resolved.is_relative_to(base_lib):
            while tool_dir.parent != base_lib and tool_dir != tool_dir.parent:
                tool_dir = tool_dir.parent
        else:
            tool_dir = resolved.parent
        return Installation(
            binary=bin_path.name,
            bin_path=bin_path,
            real_path=resolved,
            provider=self.name,
            package=tool_dir.name,
            version=None,  # a raw checkout has git history, not a release version
            root=tool_dir,
        )

    def resolve_source(self, inst: Installation) -> RepoSource | None:
        if (inst.root / ".git").exists():
            remote = _git_remote(inst.root)
            if remote:
                return RepoSource(
                    name=inst.binary,
                    target=discovery._clean_git_url(remote),
                    is_local=False,
                )
        return RepoSource(
            name=inst.binary,
            target=f"LOCAL:{inst.root}",
            is_local=True,
            local_path=inst.root,
        )

    def local_docs(self, inst: Installation) -> list[Path]:
        return find_install_root_manpages(inst.root, inst.binary)


def _git_remote(directory: Path) -> str | None:
    try:
        res = subprocess.run(
            ["git", "-C", str(directory), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug(
            "Failed getting git remote", directory=str(directory), error=str(e)
        )
    return None
