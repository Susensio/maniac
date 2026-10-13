"""A completion from the tool's repository, at exactly the installed version (ADR-0066)."""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from maniac.config import Config
from maniac.models import RepoSource
from maniac.sources.docs.completions import candidate_paths, upstream_completion


@dataclass(frozen=True)
class _Source:
    """A repository reached over `file://`: what a remote source offers here."""

    identity: str
    clone_url: str


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> RepoSource:
    """`completions/zsh/_tool` at tag v1.2.0; a later commit drops it."""
    work = tmp_path / "work"
    (work / "completions" / "zsh").mkdir(parents=True)
    (work / "completions" / "zsh" / "_tool").write_text(
        "#compdef tool\n_arguments '--old[only in 1.2.0]'\n", encoding="utf-8"
    )
    (work / "tests" / "fixtures").mkdir(parents=True)
    (work / "tests" / "fixtures" / "tool.fish").write_text(
        "complete -c tool -l fixture\n", encoding="utf-8"
    )
    _git("init", "-q", "-b", "main", cwd=work)
    _git("add", ".", cwd=work)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "1", cwd=work)
    _git("tag", "v1.2.0", cwd=work)
    _git("rm", "-q", "completions/zsh/_tool", cwd=work)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "2", cwd=work)
    return cast(RepoSource, _Source("local/tool", f"file://{work}"))


def _ask(source: RepoSource, shell: str, version: str | None, tmp_path: Path):
    return upstream_completion(
        source,
        "tool",
        shell,
        version=version,
        cache_dir=tmp_path / "cache",
        config=Config(cache_dir=tmp_path / "cache"),
    )


def test_the_completion_at_the_matching_tag_is_found(
    repository: RepoSource, tmp_path: Path
) -> None:
    found, definitive = _ask(repository, "zsh", "1.2.0", tmp_path)

    assert definitive
    assert found is not None
    assert "only in 1.2.0" in found.script


def test_no_tag_for_the_version_is_a_definitive_none(
    repository: RepoSource, tmp_path: Path
) -> None:
    """Never the default branch: its completion may offer another version's flags."""
    assert _ask(repository, "zsh", "9.9.9", tmp_path) == (None, True)


def test_no_installed_version_asks_nothing(
    repository: RepoSource, tmp_path: Path
) -> None:
    assert _ask(repository, "zsh", None, tmp_path) == (None, True)


def test_test_fixtures_are_never_a_completion(
    repository: RepoSource, tmp_path: Path
) -> None:
    found, _ = _ask(repository, "fish", "1.2.0", tmp_path)

    assert found is None


def test_an_unreachable_repository_is_not_a_definitive_none(tmp_path: Path) -> None:
    gone = cast(RepoSource, _Source("local/gone", f"file://{tmp_path / 'gone'}"))

    found, definitive = _ask(gone, "zsh", "1.2.0", tmp_path)

    assert found is None
    assert not definitive


def test_completion_directories_come_first_then_the_shallowest() -> None:
    paths = [
        "docs/_tool",
        "contrib/completion/_tool",
        "extra/deep/a/b/c/_tool",
        "completions/zsh/_tool",
        "vendor/x/_tool",
        "completions/bash/tool",
        "tool",
    ]

    assert candidate_paths(paths, "tool", "zsh") == [
        "completions/zsh/_tool",
        "contrib/completion/_tool",
        "docs/_tool",
    ]
    # A bare name counts only below a directory naming bash.
    assert candidate_paths(paths, "tool", "bash") == ["completions/bash/tool"]
