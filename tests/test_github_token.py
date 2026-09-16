import subprocess
from collections.abc import Iterator

import pytest

from maniac import github_token


@pytest.fixture(autouse=True)
def _clear_token_cache() -> Iterator[None]:
    """`resolve_github_token` is process-lifetime (see its docstring); clear
    it around each test so one test's env/subprocess patch cannot decide
    another's answer.
    """
    github_token.resolve_github_token.cache_clear()
    yield
    github_token.resolve_github_token.cache_clear()


def _unreachable(*args: object, **kwargs: object) -> object:
    raise AssertionError("gh should not have been invoked")


def test_gh_token_env_var_wins_without_spawning_gh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "from-gh-token")
    monkeypatch.setenv("GITHUB_TOKEN", "from-github-token")
    monkeypatch.setattr(github_token.subprocess, "run", _unreachable)

    assert github_token.resolve_github_token() == "from-gh-token"


def test_github_token_env_var_used_when_gh_token_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "from-github-token")
    monkeypatch.setattr(github_token.subprocess, "run", _unreachable)

    assert github_token.resolve_github_token() == "from-github-token"


def test_falls_back_to_gh_auth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert cmd == ["gh", "auth", "token", "--hostname", "github.com"]
        return subprocess.CompletedProcess(cmd, 0, stdout="from-gh-cli\n", stderr="")

    monkeypatch.setattr(github_token.subprocess, "run", fake_run)

    assert github_token.resolve_github_token() == "from-gh-cli"


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("gh"),
        OSError("boom"),
        subprocess.TimeoutExpired("gh", 5),
    ],
)
def test_gh_unavailable_degrades_to_none(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def fake_run(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(github_token.subprocess, "run", fake_run)

    assert github_token.resolve_github_token() is None


def test_gh_nonzero_exit_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="not authenticated"
        )

    monkeypatch.setattr(github_token.subprocess, "run", fake_run)

    assert github_token.resolve_github_token() is None


def test_gh_empty_stdout_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="\n", stderr="")

    monkeypatch.setattr(github_token.subprocess, "run", fake_run)

    assert github_token.resolve_github_token() is None


def test_resolution_is_memoized_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "first")
    assert github_token.resolve_github_token() == "first"

    monkeypatch.setenv("GH_TOKEN", "second")
    assert github_token.resolve_github_token() == "first"


def test_env_github_token_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "from-env\n")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    assert github_token.resolve_github_token() == "from-env"


def test_whitespace_only_env_github_token_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "   \n")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="from-gh-cli\n", stderr="")

    monkeypatch.setattr(github_token.subprocess, "run", fake_run)

    assert github_token.resolve_github_token() == "from-gh-cli"
