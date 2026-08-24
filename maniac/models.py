from dataclasses import dataclass
from pathlib import Path


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
        return f"https://github.com/{self.target}.git"


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
