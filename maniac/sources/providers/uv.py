"""uv tool installs under `~/.local/share/uv/tools/` (ADR-0015)."""

import json
from pathlib import Path

from ...config import Config
from ...logging import logger
from ...models import Installation, RepoSource
from ..manpages import find_install_root_manpages
from ..pathcache import resolve_cached

_TOOLS_MARKER = "/.local/share/uv/tools/"


class UvProvider:
    """Detects a `uv tool install`; resolves only the local-editable case uv itself
    records -- a published package has no upstream repository recorded anywhere
    in the install, so it is left unresolved, matching prior behaviour.
    """

    name = "uv"

    def can_detect(self, real_path: Path) -> bool:
        """Recognise uv's documented per-tool venv layout."""
        return _TOOLS_MARKER in str(real_path)

    def detect(self, bin_path: Path) -> Installation | None:
        resolved = resolve_cached(bin_path)
        resolved_str = str(resolved)
        if _TOOLS_MARKER not in resolved_str:
            return None
        prefix, _, tail = resolved_str.partition(_TOOLS_MARKER)
        tool = tail.split("/", 1)[0]
        root = Path(prefix + _TOOLS_MARKER + tool)
        return Installation(
            binary=bin_path.name,
            bin_path=bin_path,
            real_path=resolved,
            provider=self.name,
            package=tool,
            version=_installed_version(root, tool),
            root=root,
        )

    def resolve_source(
        self, inst: Installation, *, config: Config
    ) -> RepoSource | None:
        local_dir = _local_editable_dir(inst.root)
        if local_dir is None:
            return None
        return RepoSource(
            name=inst.binary,
            target=f"LOCAL:{local_dir}",
            is_local=True,
            local_path=local_dir,
        )

    def local_docs(self, inst: Installation) -> list[Path]:
        return find_install_root_manpages(inst.root, inst.binary)


def _installed_version(root: Path, tool: str) -> str | None:
    """Read the tool's own version from its dist-info directory name, if findable.

    uv's dist-info directory name normalizes the distribution name (`-`/`_`/`.`
    interchangeably); compare with separators collapsed rather than assume an
    exact match against the tool directory's own name.
    """
    normalized_tool = _normalize(tool)
    for dist_info in root.glob("lib/**/site-packages/*.dist-info"):
        name, _, version = dist_info.stem.rpartition("-")
        if _normalize(name) == normalized_tool:
            return version
    return None


def _normalize(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def _local_editable_dir(root: Path) -> Path | None:
    """Return the local checkout an editable uv-tool install points at, if any.

    Checks every dist-info under the tool's venv, not only the entrypoint
    package's own -- mirrors `discovery._find_uv_tool_local_dir`'s prior scope,
    since that is the extent of the evidence uv itself records for the install.
    """
    for dist_info in root.glob("lib/**/site-packages/*.dist-info"):
        direct_url = dist_info / "direct_url.json"
        if not direct_url.exists():
            continue
        try:
            data = json.loads(direct_url.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.debug(
                "Error reading uv direct_url.json", path=str(direct_url), error=str(e)
            )
            continue
        raw_url = data.get("url", "")
        if raw_url.startswith("file://"):
            local_path = Path(raw_url.removeprefix("file://"))
            if local_path.exists():
                return local_path
    return None
