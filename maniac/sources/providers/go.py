"""`go install` binaries under `$GOBIN` or `~/go/bin` (ADR-0015 Stage 4).

Identity comes from `go version -m <bin>`, a subprocess against the local
binary's embedded build metadata -- no network, no registry.
"""

import os
import shutil
import subprocess
from functools import cache
from pathlib import Path

from ...config import Config
from ...logging import logger
from ...models import Installation, RepoSource
from ..manpages import find_install_root_manpages
from ..pathcache import resolve_cached


class GoProvider:
    """Detects a `go install`ed binary and reads its main package's import
    path and module version straight from its embedded build info.
    """

    name = "go"

    def detect(self, bin_path: Path) -> Installation | None:
        gobin, resolved_gobin = _gobin_paths(
            os.environ.get("GOBIN"),
            os.environ.get("GOPATH"),
            os.environ.get("HOME"),
        )
        resolved = resolve_cached(bin_path)
        try:
            if resolved.parent != resolved_gobin:
                return None
        except OSError:
            return None
        if not resolved.is_file():
            return None
        info = _read_module_info(resolved)
        if info is None:
            return None
        import_path, _mod_path, version = info
        return Installation(
            binary=bin_path.name,
            bin_path=bin_path,
            real_path=resolved,
            provider=self.name,
            package=import_path,
            version=version,
            root=gobin,
        )

    def resolve_source(
        self, inst: Installation, *, config: Config
    ) -> RepoSource | None:
        info = _read_module_info(inst.real_path)
        if info is None:
            return None
        _import_path, mod_path, _version = info
        if not mod_path.startswith("github.com/"):
            return None
        segments = mod_path.removeprefix("github.com/").split("/")
        if len(segments) < 2:
            return None
        return RepoSource(
            name=inst.binary, target="/".join(segments[:2]), is_local=False
        )

    def local_docs(self, inst: Installation) -> list[Path]:
        return find_install_root_manpages(inst.root, inst.binary)


@cache
def _gobin_paths(
    gobin_env: str | None, gopath_env: str | None, home: str | None
) -> tuple[Path, Path]:
    """`$GOBIN`, then `$GOPATH/bin`, then `~/go/bin` -- go's own resolution order.

    `GOPATH` may list several `os.pathsep`-separated directories; `go install`
    only ever uses the first one's `bin`.
    """
    if gobin_env:
        gobin = Path(gobin_env).expanduser()
    else:
        base = (
            Path(gopath_env.split(os.pathsep)[0]).expanduser()
            if gopath_env
            else Path(home).expanduser() / "go"
            if home
            else Path.home() / "go"
        )
        gobin = base / "bin"
    return gobin, resolve_cached(gobin)


def _read_module_info(path: Path) -> tuple[str, str, str | None] | None:
    """Return `(import_path, module_path, version)` from `go version -m <path>`.

    The first `mod` line is the main module (go's own output order); its
    version is `None` for `(devel)`, an unreleased local build.
    """
    go_bin = shutil.which("go")
    if go_bin is None:
        return None
    try:
        result = subprocess.run(
            [go_bin, "version", "-m", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("Error running go version -m", path=str(path), error=str(e))
        return None
    if result.returncode != 0:
        return None
    import_path: str | None = None
    mod_path: str | None = None
    version: str | None = None
    for line in result.stdout.splitlines():
        if not line.startswith("\t"):
            continue
        fields = line.strip("\t").split("\t")
        if len(fields) >= 2 and fields[0] == "path":
            import_path = fields[1]
        elif len(fields) >= 3 and fields[0] == "mod" and mod_path is None:
            mod_path, version = fields[1], fields[2]
    if import_path is None:
        return None
    return import_path, mod_path or "", (None if version == "(devel)" else version)
