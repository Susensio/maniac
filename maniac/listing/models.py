"""The facts one `list` row carries, independent of how any of them render."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from ..models import RepoSource

if TYPE_CHECKING:
    from ..models import Installation
    from ..sources.providers.base import Provider


class ActionState(Enum):
    """The action-ladder state a binary's manpage reachability puts it in (ADR-0026)."""

    OK = "ok"
    UNVERIFIED = "unverified"
    OUTDATED = "outdated"
    AVAILABLE = "available"
    MISSING = "missing"
    ERROR = "error"
    """This tool's own metadata file is present but malformed (ADR-0060) --
    distinct from MISSING, which means nothing is wrong, just nothing found."""


class PageSource(Enum):
    """Where page content came from, or would come from if installed (ADR-0027).

    One column serves both readings -- State already disambiguates which
    applies, since `ok`/`outdated` describe a page that resolves and
    `available` describes one that would if installed.
    """

    MANIAC = "maniac"
    VENDOR = "vendor"
    UPSTREAM = "upstream"
    SYSTEM = "system"
    NONE = ""


@dataclass(frozen=True, slots=True)
class ToolError:
    """A tool's own broken evidence, carried as `MalformedToolMetadata` and
    `NotGloballySelected` raise it (ADR-0060, ADR-0061): the file or
    resolved root at fault, and why, kept apart so a renderer can show the
    reason without the path first mangling it (`row.error` used to be one
    pre-joined string, and `rsplit("/", 1)` on a reason that itself
    contained a path lost the reason)."""

    path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class Candidate:
    """One binary to report on, with the provider claim behind it, if any.

    A named tool no provider claims still gets a row (ADR-0013), so both
    `provider` and `installation` are optional and always absent together.
    """

    tool: str
    provider: "Provider | None"
    installation: "Installation | None"
    error: ToolError | None = None
    """Set when discovery itself raised `MalformedToolMetadata` or
    `NotGloballySelected` (ADR-0060, ADR-0061) for this tool, before any
    provider or installation could be resolved."""

    @property
    def package(self) -> str:
        """The installing package, falling back to the binary's own name."""
        return self.installation.package if self.installation is not None else self.tool

    @property
    def provider_name(self) -> str:
        """The claiming provider's name, or empty for an unclaimed binary."""
        return self.provider.name if self.provider is not None else ""


@dataclass(frozen=True, slots=True)
class ToolRow:
    """One binary's reachability. `package` groups siblings for the table view only."""

    tool: str
    package: str
    provider: str
    state: ActionState
    source: PageSource
    upstream: RepoSource | None
    managed: bool = False
    page_path: Path | None = None
    page_uri: str | None = None
    owning_package: str | None = None
    target_cluster: int | None = None
    drift: bool = False
    """Manifest has an entry for this tool, but its manpath link is broken --
    a page deleted by hand, `output_dir`/`backup_dir` cleaned, or another
    installer overwriting the link (ADR-0046's structural scan)."""
    error: ToolError | None = None
    """Set together with `state is ActionState.ERROR`: the file and reason
    a provider could not read this tool's own metadata, or a Mise install
    refused as project-only (ADR-0060, ADR-0061)."""


@dataclass(frozen=True, slots=True)
class LocalClassification:
    """One binary's state and page provenance from local evidence alone."""

    state: ActionState
    source: PageSource
    managed: bool
    page_path: Path | None
    page_uri: str | None = None
    owning_package: str | None = None
    drift: bool = False
    error: ToolError | None = None


RowSnapshot = tuple[ToolRow, ...]
"""An ordered, immutable view of every row at one moment (ADR-0024)."""
