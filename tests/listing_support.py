"""Shared stand-ins for the `list` inventory seam and its terminal adapter."""

from pathlib import Path
from typing import Any

from maniac.config import Config
from maniac.listing import Candidate, InventoryObserver, RowSnapshot
from maniac.models import Installation, RepoSource
from maniac.sources.providers.base import SourceResolver


class _FakeProvider:
    """Minimal `Provider` stand-in with per-test-configurable answers."""

    def __init__(
        self,
        name: str = "fake",
        local_docs: list[Path] | None = None,
        source: RepoSource | None = None,
    ) -> None:
        self.name = name
        self._local_docs = local_docs or []
        self._source = source

    def detect(self, bin_path: Path) -> Installation | None:
        return None

    def resolve_source(
        self, inst: Installation, *, config: Config, sources: SourceResolver
    ) -> RepoSource | None:
        return self._source

    def local_docs(self, inst: Installation) -> list[Path]:
        return self._local_docs


def _installation(
    binary: str = "tool",
    package: str = "tool",
    version: str | None = "1.2.3",
    root: Path = Path("/root"),
) -> Installation:
    return Installation(
        binary=binary,
        bin_path=Path(f"/bin/{binary}"),
        real_path=Path(f"/bin/{binary}"),
        provider="fake",
        package=package,
        version=version,
        root=root,
    )


def _candidate(
    provider: Any = None, inst: Installation | None = None, tool: str = "tool"
) -> Candidate:
    return Candidate(tool=tool, provider=provider, installation=inst)


def _config(tmp_path: Path) -> Config:
    return Config(man_dir=tmp_path / "man" / "man1", cache_dir=tmp_path / "repos")


class RecordingObserver(InventoryObserver):
    """Keeps every inventory event, in arrival order, for later assertions."""

    def __init__(self) -> None:
        self.discovery_totals: list[int] = []
        self.discovery_scans = 0
        self.row_totals: list[int] = []
        self.row_scans = 0
        self.inventories: list[RowSnapshot] = []
        self.classified: list[tuple[RowSnapshot, int, bool]] = []
        self.local_facts: list[tuple[RowSnapshot, set[int]]] = []
        self.upstream_groups: list[tuple[RowSnapshot, set[int]]] = []
        self.idles = 0

    def discovery_started(self, total: int) -> None:
        self.discovery_totals.append(total)

    def discovery_scanned(self) -> None:
        self.discovery_scans += 1

    def rows_started(self, total: int) -> None:
        self.row_totals.append(total)

    def row_scanned(self) -> None:
        self.row_scans += 1

    def inventory_ready(self, rows: RowSnapshot) -> None:
        self.inventories.append(rows)

    def row_classified(
        self, rows: RowSnapshot, index: int, upstream_pending: bool
    ) -> None:
        self.classified.append((rows, index, upstream_pending))

    def local_facts_ready(self, rows: RowSnapshot, upstream_pending: set[int]) -> None:
        self.local_facts.append((rows, upstream_pending))

    def upstream_group_ready(self, rows: RowSnapshot, indexes: set[int]) -> None:
        self.upstream_groups.append((rows, indexes))

    def idle(self) -> None:
        self.idles += 1
