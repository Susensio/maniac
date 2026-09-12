"""`cargo install` binaries under `$CARGO_HOME/bin/` (ADR-0015 Stage 4).

Reads `$CARGO_HOME` from the environment, never `~/.cargo` -- ADR-0015 records
it as wrong on the development system (`~/.local/share/cargo` there).
"""

import json
import os
from functools import cache
from pathlib import Path

from ...config import Config
from ...logging import logger
from ...models import Installation, RepoSource
from ..manpages import find_install_root_manpages
from ..pathcache import resolve_cached


class CargoProvider:
    """Detects a `cargo install`ed binary by membership in `.crates2.json`'s
    `installs`, not merely by living in `$CARGO_HOME/bin` -- rustup ships its
    own shims there (`cargo`, `rustc`, `rust-analyzer`, ...), none a `cargo
    install` and none listed in `.crates2.json`.
    """

    name = "cargo"

    def can_detect(self, real_path: Path) -> bool:
        """Recognise Cargo's configured executable directory before parsing metadata."""
        cargo_home = _cargo_home(os.environ.get("CARGO_HOME"), os.getcwd())
        return cargo_home is not None and real_path.parent == cargo_home / "bin"

    def detect(self, bin_path: Path) -> Installation | None:
        cargo_home = _cargo_home(os.environ.get("CARGO_HOME"), os.getcwd())
        if cargo_home is None:
            return None
        cargo_bin = cargo_home / "bin"
        resolved = resolve_cached(bin_path)
        if resolved.parent != cargo_bin:
            return None
        crate = _find_crate(cargo_home, resolved.name)
        if crate is None:
            return None
        name, version = crate
        return Installation(
            binary=bin_path.name,
            bin_path=bin_path,
            real_path=resolved,
            provider=self.name,
            package=name,
            version=version,
            root=cargo_bin,
        )

    def resolve_source(
        self, inst: Installation, *, config: Config
    ) -> RepoSource | None:
        # `.crates2.json` records no upstream repository -- crates.io itself
        # would have to be queried, and nothing here guesses one.
        return None

    def local_docs(self, inst: Installation) -> list[Path]:
        return find_install_root_manpages(inst.root, inst.binary)


@cache
def _cargo_home(cargo_home_env: str | None, cwd: str) -> Path | None:
    """Normalized Cargo home for one environment and working directory."""
    if not cargo_home_env:
        return None
    home = Path(cargo_home_env).expanduser()
    return (home if home.is_absolute() else Path(cwd) / home).resolve()


def _find_crate(cargo_home: Path, binary_name: str) -> tuple[str, str] | None:
    """Return `(crate, version)` for the crate whose `.crates2.json` entry
    lists `binary_name` among its `bins`, or None if no entry does.
    """
    return _crates_by_binary(cargo_home / ".crates2.json").get(binary_name)


@cache
def _crates_by_binary(crates2_path: Path) -> dict[str, tuple[str, str]]:
    """Map Cargo-installed executable names to their crate and version.

    Cargo metadata cannot change during one CLI invocation. Parsing it once
    avoids reading `.crates2.json` for every executable examined from `$PATH`.
    """
    try:
        data = json.loads(crates2_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.debug(
            "Error reading cargo .crates2.json", path=str(crates2_path), error=str(e)
        )
        return {}
    installs = data.get("installs")
    if not isinstance(installs, dict):
        return {}
    crates: dict[str, tuple[str, str]] = {}
    for key, entry in installs.items():
        if not isinstance(entry, dict):
            continue
        name, _, rest = key.partition(" ")
        version, _, _source = rest.partition(" ")
        bins = entry.get("bins")
        if not name or not version or not isinstance(bins, list):
            continue
        for binary in bins:
            if isinstance(binary, str):
                crates.setdefault(binary, (name, version))
    return crates
