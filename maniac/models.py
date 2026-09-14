"""Data transfer objects for maniac."""

import re
from dataclasses import dataclass, field
from pathlib import Path

# A Mise config's `tool_alias` can name any backend ("aqua:", "npm:", "pipx:",
# "cargo:", ...), and only "github:" is stripped before it reaches `target`
# (see sources/discovery.py's `_check_mise_toml`). Matches a leading
# `backend:` so the rest can be told apart from a bare "owner/repo".
_BACKEND_PREFIX = re.compile(r"^([a-zA-Z][\w-]*):(.+)$")


def _clone_url_for_identifier(identifier: str) -> str | None:
    """Return the only clone URL justified by one remote identity."""
    if identifier.startswith("LOCAL:"):
        raise ValueError("a remote source cannot use a reserved local identity")
    if identifier.startswith(("http://", "https://")):
        return identifier
    prefix_match = _BACKEND_PREFIX.match(identifier)
    if prefix_match:
        backend, package = prefix_match.groups()
        if backend != "aqua":
            return None
        segments = package.split("/")
        return (
            f"https://github.com/{'/'.join(segments[:2])}.git"
            if len(segments) >= 2
            else None
        )
    return f"https://github.com/{identifier}.git" if "/" in identifier else None


class _RepoSourceMeta(type):
    """Build a concrete source without exposing the retired field bundle."""

    def __call__(
        cls, *args: object, **kwargs: object
    ) -> "LocalRepoSource | RemoteRepoSource":
        if cls is not RepoSource:
            return super().__call__(*args, **kwargs)
        name = kwargs.get("name", args[0] if args else None)
        target = kwargs.get("target", args[1] if len(args) > 1 else None)
        is_local = kwargs.get("is_local", args[2] if len(args) > 2 else None)
        local_path = kwargs.get("local_path", args[3] if len(args) > 3 else None)
        if (
            not isinstance(name, str)
            or not isinstance(target, str)
            or not isinstance(is_local, bool)
        ):
            raise TypeError("RepoSource needs name, target, and is_local")
        if local_path is not None and not isinstance(local_path, Path):
            raise TypeError("a local path must be a Path")
        if is_local:
            if local_path is not None and target != f"LOCAL:{local_path}":
                raise ValueError("a local source target must name its local path")
            if not target.startswith("LOCAL:"):
                raise ValueError("a local source target must start with LOCAL:")
            return LocalRepoSource(
                name, local_path or Path(target.removeprefix("LOCAL:"))
            )
        if local_path is not None or target.startswith("LOCAL:"):
            raise ValueError("a remote source cannot carry a local path")
        return RemoteRepoSource.from_identifier(name, target)


class RepoSource(metaclass=_RepoSourceMeta):
    """Compatibility constructor for a validated local or remote source.

    New production code constructs ``LocalRepoSource`` or ``RemoteRepoSource``
    directly.  Keeping this boundary accepts old serialized/test-shaped input
    while rejecting the impossible combinations that its old correlated fields
    permitted.
    """

    def __init__(
        self,
        name: str,
        target: str,
        is_local: bool,
        local_path: Path | None = None,
    ) -> None:
        raise TypeError("RepoSource constructs a local or remote variant")

    name: str
    target: str
    local_path: Path | None

    @property
    def identity(self) -> str:
        raise NotImplementedError

    @property
    def clone_url(self) -> str | None:
        raise NotImplementedError

    @property
    def is_local(self) -> bool:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class LocalRepoSource(RepoSource):
    """A checkout on this machine, identified solely by its path."""

    name: str
    path: Path

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a source name cannot be empty")

    @property
    def identity(self) -> str:
        """Canonical local identity retained for presentation and provenance."""
        return f"LOCAL:{self.path}"

    @property
    def clone_url(self) -> None:
        return None

    @property
    def is_local(self) -> bool:
        return True

    @property
    def local_path(self) -> Path:
        return self.path

    @property
    def target(self) -> str:
        """Legacy serialized/display spelling of ``identity``."""
        return self.identity


@dataclass(frozen=True, slots=True)
class RemoteRepoSource(RepoSource):
    """A remote source with display identity and already-resolved clone data."""

    name: str
    identity: str
    clone_url: str | None

    def __post_init__(self) -> None:
        if not self.name or not self.identity:
            raise ValueError("a remote source needs a name and identity")
        expected_clone_url = _clone_url_for_identifier(self.identity)
        if self.clone_url != expected_clone_url:
            raise ValueError("a remote source clone URL must match its identity")

    @classmethod
    def from_identifier(cls, name: str, identifier: str) -> "RemoteRepoSource":
        """Normalize a discovered identity once, retaining only a justified clone URL."""
        return cls(name, identifier, _clone_url_for_identifier(identifier))

    @property
    def target(self) -> str:
        """Legacy serialized/display spelling of ``identity``."""
        return self.identity

    @property
    def is_local(self) -> bool:
        return False

    @property
    def local_path(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class Installation:
    """A binary as a provider (ADR-0015) detected it: where it lives and who installed it."""

    binary: str  # "hx"
    bin_path: Path  # ~/.local/bin/hx, the symlink or real file
    real_path: Path  # what it resolves to
    provider: str  # "mise"
    package: str  # identity in the provider's namespace
    version: str | None  # "25.01"
    root: Path  # install root; docs may live under it
    parent: "Installation | None" = None  # mise -> its backend


@dataclass
class DocFile:
    rel_path: str
    content: str


@dataclass(slots=True)
class CoverageStats:
    """Structured coverage statistics for CLI subcommands and flags."""

    found_cmds: list[str]
    missing_cmds: list[str]
    cmd_pct: float
    found_flags: list[str]
    missing_flags: list[str]
    flag_pct: float


@dataclass
class PipelineResult:
    tool_name: str
    repo_source: RepoSource | None
    command_count: int
    doc_file_count: int
    context_path: Path | None
    prompt_path: Path | None
    markdown_path: Path
    roff_path: Path | None
    installed_path: Path | None
    markdown_content: str


@dataclass
class EvaluationResult:
    score: int
    passed: bool
    rubric_breakdown: dict[str, int]
    defects: list[str] = field(default_factory=list)
    summary: str = ""
    deterministic_passed: bool = True
    deterministic_defects: list[str] = field(default_factory=list)
    coverage: CoverageStats | None = None


@dataclass
class ComparisonResult:
    """Head-to-head evaluation of an installed manpage against MANIAC's generated one."""

    tool_name: str
    installed: EvaluationResult
    generated: EvaluationResult
    winner: str
    differences: str
    installed_strengths: list[str] = field(default_factory=list)
    generated_strengths: list[str] = field(default_factory=list)
