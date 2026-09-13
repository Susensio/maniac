"""pipx installs under `$PIPX_HOME/venvs/<pkg>/` (ADR-0015 Stage 4)."""

import email
import os
from functools import cache
from pathlib import Path

from ...config import Config
from ...logging import logger
from ...models import Installation, RepoSource
from .. import discovery
from ..manpages import find_install_root_manpages
from ..pathcache import resolve_cached


class PipxProvider:
    """Detects a `pipx install`; same shape as `UvProvider` -- a venv per tool --
    with identity read from the dist-info `METADATA` pipx itself installed,
    rather than inferred from the venv directory name.
    """

    name = "pipx"

    def can_detect(self, real_path: Path) -> bool:
        """Recognise one of pipx's configured venv roots without metadata I/O."""
        real_path_str = str(real_path)
        return any(
            f"{home}/venvs/" in real_path_str
            for home in _pipx_home_candidates(
                os.environ.get("PIPX_HOME"),
                os.environ.get("XDG_DATA_HOME"),
                str(Path.home()),
            )
        )

    def detect(self, bin_path: Path) -> Installation | None:
        resolved = resolve_cached(bin_path)
        resolved_str = str(resolved)
        for home in _pipx_home_candidates(
            os.environ.get("PIPX_HOME"),
            os.environ.get("XDG_DATA_HOME"),
            str(Path.home()),
        ):
            marker = f"{home}/venvs/"
            if marker not in resolved_str:
                continue
            prefix, _, tail = resolved_str.partition(marker)
            package = tail.split("/", 1)[0]
            if not package:
                continue
            root = Path(prefix + marker + package)
            metadata = find_distribution_metadata(root, package)
            version = metadata.get("Version") if metadata else None
            return Installation(
                binary=bin_path.name,
                bin_path=bin_path,
                real_path=resolved,
                provider=self.name,
                package=package,
                version=version,
                root=root,
            )
        return None

    def resolve_source(
        self, inst: Installation, *, config: Config
    ) -> RepoSource | None:
        metadata = find_distribution_metadata(inst.root, inst.package)
        if metadata is None:
            return None
        repo = repository_from_metadata(metadata)
        return (
            RepoSource(name=inst.binary, target=repo, is_local=False) if repo else None
        )

    def local_docs(self, inst: Installation) -> list[Path]:
        return find_install_root_manpages(inst.root, inst.binary)


@cache
def _pipx_home_candidates(
    pipx_home: str | None, xdg_data_home: str | None, home: str
) -> tuple[str, ...]:
    """`$PIPX_HOME`, then pipx's own documented defaults, in the order pipx
    itself resolves them: an explicit env var, then the XDG data dir, then
    the pre-1.0 fallback location pipx still honours if it exists.
    """
    if pipx_home:
        return (str(Path(pipx_home).expanduser()),)
    default = (
        Path(xdg_data_home).expanduser()
        if xdg_data_home
        else Path(home) / ".local/share"
    )
    return (str(default / "pipx"), str(Path(home) / ".local/pipx"))


@cache
def find_distribution_metadata(
    root: Path, package: str
) -> email.message.Message | None:
    """Locate the dist-info `METADATA` matching `package` under a pipx venv.

    Mirrors `UvProvider`'s glob across `lib/**/site-packages/*.dist-info`,
    comparing distribution names with separators collapsed since dist-info
    directory names normalize `-`/`_`/`.` interchangeably.
    """
    normalized = _normalize(package)
    for dist_info in root.glob("lib/**/site-packages/*.dist-info"):
        name, _, _version = dist_info.stem.rpartition("-")
        if _normalize(name) != normalized:
            continue
        metadata_path = dist_info / "METADATA"
        try:
            text = metadata_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.debug(
                "Error reading pipx dist-info METADATA",
                path=str(metadata_path),
                error=str(e),
            )
            continue
        return email.message_from_string(text)
    return None


def _normalize(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


_REPO_LABELS = ("repository", "source", "source code", "github")


def repository_from_metadata(metadata: email.message.Message) -> str | None:
    """Read an explicit GitHub link from `Project-URL`/`Home-page`, never guessed.

    `Project-URL` entries are "Label, URL" pairs, and a package lists several
    -- documentation, homepage, repository, ... A label naming the repository
    or source wins over any other GitHub-looking entry (e.g. a docs mirror
    hosted on GitHub too), which in turn wins over an unlabelled `Home-page`.
    """
    fallback: str | None = None
    for raw in metadata.get_all("Project-URL") or []:
        label, _, url = raw.partition(",")
        url = url.strip()
        if not url:
            continue
        cleaned = discovery._clean_git_url(url)
        if cleaned == url:
            continue
        if label.strip().lower() in _REPO_LABELS:
            return cleaned
        if fallback is None:
            fallback = cleaned
    if fallback:
        return fallback
    home_page = metadata.get("Home-page")
    if home_page:
        cleaned = discovery._clean_git_url(home_page)
        if cleaned != home_page:
            return cleaned
    return None
