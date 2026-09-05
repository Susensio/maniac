"""Data transfer objects for maniac."""

import re
from dataclasses import dataclass, field
from pathlib import Path

# A Mise config's `tool_alias` can name any backend ("aqua:", "npm:", "pipx:",
# "cargo:", ...), and only "github:" is stripped before it reaches `target`
# (see sources/discovery.py's `_check_mise_toml`). Matches a leading
# `backend:` so the rest can be told apart from a bare "owner/repo".
_BACKEND_PREFIX = re.compile(r"^([a-zA-Z][\w-]*):(.+)$")


@dataclass
class RepoSource:
    name: str
    target: str  # "owner/repo", "LOCAL:/path", "https://...", or "backend:identifier"
    is_local: bool
    local_path: Path | None = None

    @property
    def clone_url(self) -> str | None:
        """GitHub clone URL for a bare "owner/repo" shorthand, or None if not resolvable.

        Aqua's package identifier is itself an "owner/repo" pair, so an
        "aqua:" prefix is stripped and linked; every other backend
        ("npm:", "pipx:", "cargo:", ...) names a registry package with no
        fixed relationship to a GitHub path, so guessing a link there would
        point at the wrong repository rather than none.
        """
        if self.is_local:
            return None
        if self.target.startswith(("http://", "https://")):
            return self.target

        target = self.target
        prefix_match = _BACKEND_PREFIX.match(target)
        if prefix_match:
            backend, rest = prefix_match.group(1), prefix_match.group(2)
            if backend != "aqua":
                return None
            segments = rest.split("/")
            if len(segments) < 2:
                return None
            target = "/".join(segments[:2])

        if "/" in target:
            return f"https://github.com/{target}.git"
        return None


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
    repo_source: RepoSource
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
