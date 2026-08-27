"""Data transfer objects for maniac."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RepoSource:
    name: str
    target: str  # "owner/repo" or "LOCAL:/path" or "https://..."
    is_local: bool
    local_path: Path | None = None

    @property
    def clone_url(self) -> str | None:
        if self.is_local:
            return None
        if self.target.startswith(("http://", "https://")):
            return self.target
        if "/" in self.target:
            return f"https://github.com/{self.target}.git"
        return None


@dataclass
class DocFile:
    rel_path: str
    content: str


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
    coverage: Any | None = None
