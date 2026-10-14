"""A recorded repository that points at the tool's own (ADR-0067)."""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from maniac.config import Config
from maniac.models import Installation, RepoSource
from maniac.sources.docs import redirect, repository
from maniac.sources.docs.redirect import Redirect, detect, is_asked
from maniac.sources.lookup.candidates import Decided, repository_of

from .listing_support import _FakeProvider

_WRAPPER = RepoSource(
    name="pyright", target="RobertCraigie/pyright-python", is_local=False
)


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(cache_dir=tmp_path / "cache", config_dir=tmp_path / "config")


@dataclass
class _Remote:
    """A fake remote: `files` is the recorded repository's tree, `tags` each
    repository's tag for the version; `asked` counts tag lookups."""

    files: dict[str, bytes] = field(
        default_factory=lambda: {
            "README.md": b"Wraps https://github.com/microsoft/pyright ."
        }
    )
    tags: dict[str, str] = field(
        default_factory=lambda: {
            "RobertCraigie/pyright-python": "v1.1.414",
            "microsoft/pyright": "1.1.414",
        }
    )
    asked: int = 0


@pytest.fixture
def git(monkeypatch: pytest.MonkeyPatch) -> _Remote:
    remote = _Remote()

    def tag(cache_dir: Path, clone_url: str, version: str, config: Config):
        remote.asked += 1
        identity = clone_url.removeprefix("https://github.com/").removesuffix(".git")
        return remote.tags.get(identity), True

    monkeypatch.setattr(repository, "_find_matching_tag_cached_result", tag)
    monkeypatch.setattr(repository, "_fetch_bare_ref", lambda *a, **k: "refs/maniac/x")
    monkeypatch.setattr(
        repository, "_git_stdout", lambda cmd, cfg: "\n".join(remote.files) + "\n"
    )
    monkeypatch.setattr(
        repository,
        "_git_bytes",
        lambda cmd, cfg: remote.files[cmd[-1].split(":", 1)[1]],
    )
    return remote


def _detect(cfg: Config, source: RepoSource = _WRAPPER, **kwargs: str):
    args = {"binary": "pyright", "package": "pyright", "version": "1.1.414"} | kwargs
    return detect(source, cache_dir=cfg.cache_dir, config=cfg, **args)


def test_a_repository_named_like_its_tool_is_never_searched() -> None:
    black = RepoSource(name="black", target="psf/black", is_local=False)
    dmypy = RepoSource(name="dmypy", target="python/mypy", is_local=False)

    assert not is_asked(black, "black", "black")
    assert not is_asked(dmypy, "dmypy", "mypy")  # named like its package
    assert is_asked(_WRAPPER, "pyright", "pyright")


def test_a_wrapper_pointing_at_the_tools_repository_is_followed(
    cfg: Config, git: _Remote
) -> None:
    found, final = _detect(cfg)

    assert final
    assert found == Redirect("microsoft/pyright", 1, ("README.md",), "1.1.414")
    assert "links microsoft/pyright once (README.md)" in found.evidence(_WRAPPER)


def test_build_scripts_count_and_the_most_linked_wins(
    cfg: Config, git: _Remote
) -> None:
    """tmux-builds: its README links tmux/tmux once, its scripts clone it."""
    builds = RepoSource(name="tmux", target="tmux/tmux-builds", is_local=False)
    git.files = {
        "README.md": b"Static builds of https://github.com/tmux/tmux, see also "
        b"https://github.com/someone/tmux",
        "scripts/build_tmux.sh": b"git clone https://github.com/tmux/tmux.git",
        "src/unrelated.c": b"https://github.com/someone/tmux",
    }
    git.tags = {"tmux/tmux": "3.7a", "someone/tmux": "3.7a"}

    found, _ = _detect(cfg, builds, binary="tmux", package="tmux", version="3.7a")

    assert found is not None
    assert (found.target, found.links) == ("tmux/tmux", 2)
    assert found.files == ("README.md", "scripts/build_tmux.sh")


def test_no_tag_for_the_installed_version_means_no_redirect(
    cfg: Config, git: _Remote
) -> None:
    """ADR-0016: never trade a version-matched source for an unmatched one."""
    git.tags = {"RobertCraigie/pyright-python": "v1.1.414"}

    assert _detect(cfg) == (None, True)


def test_an_answer_is_asked_once(cfg: Config, git: _Remote) -> None:
    first = _detect(cfg)
    asked = git.asked

    assert _detect(cfg) == first
    assert git.asked == asked


def test_a_check_that_did_not_finish_is_not_remembered(
    cfg: Config, git: _Remote, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repository, "_fetch_bare_ref", lambda *a, **k: None)

    assert _detect(cfg) == (None, False)
    monkeypatch.setattr(repository, "_fetch_bare_ref", lambda *a, **k: "refs/maniac/x")
    assert _detect(cfg)[0] is not None


def _inst() -> Installation:
    return Installation(
        binary="pyright",
        bin_path=Path("/bin/pyright"),
        real_path=Path("/bin/pyright"),
        provider="fake",
        package="pyright",
        version="1.1.414",
        root=Path("/root"),
    )


@pytest.fixture
def detected(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []

    def fake(source: RepoSource, **kwargs: object):
        asked.append(source.identity)
        return Redirect("microsoft/pyright", 4, ("README.md",), "1.1.414"), True

    monkeypatch.setattr(redirect, "detect", fake)
    return asked


def test_detection_replaces_the_record_and_says_so(
    cfg: Config, detected: list[str]
) -> None:
    found = repository_of(_FakeProvider(source=_WRAPPER), _inst(), cfg)

    assert found.source is not None and found.source.identity == "microsoft/pyright"
    assert found.recorded == _WRAPPER
    assert found.decided is Decided.DETECTED and found.redirected
    assert found.evidence is not None and "4 times" in found.evidence


def test_the_users_table_outranks_detection_both_ways(
    cfg: Config, detected: list[str]
) -> None:
    cfg.documentation_repository_overrides[_WRAPPER.identity] = _WRAPPER.identity
    kept = repository_of(_FakeProvider(source=_WRAPPER), _inst(), cfg)
    cfg.documentation_repository_overrides[_WRAPPER.identity] = "other/pyright"
    pinned = repository_of(_FakeProvider(source=_WRAPPER), _inst(), cfg)

    assert kept.source == _WRAPPER and not kept.redirected
    assert kept.decided is Decided.CONFIG
    assert pinned.source is not None and pinned.source.identity == "other/pyright"
    assert pinned.decided is Decided.CONFIG
    assert detected == []


def test_why_and_install_say_who_decided_and_how_to_overrule_it(
    cfg: Config, detected: list[str]
) -> None:
    from maniac.orchestration.context import redirect_lines

    found = repository_of(_FakeProvider(source=_WRAPPER), _inst(), cfg)
    detected_lines = redirect_lines(found, cfg)
    cfg.documentation_repository_overrides[_WRAPPER.identity] = _WRAPPER.identity
    kept = redirect_lines(
        repository_of(_FakeProvider(source=_WRAPPER), _inst(), cfg), cfg
    )

    assert detected_lines[0].startswith(
        "repository: microsoft/pyright, not RobertCraigie/pyright-python"
    )
    assert "links microsoft/pyright 4 times" in detected_lines[0]
    assert detected_lines[1].startswith("to keep RobertCraigie/pyright-python, add")
    table = f"[documentation_repositories] in {cfg.config_dir / 'config.toml'}"
    assert kept == [f"repository: RobertCraigie/pyright-python, kept by {table}"]


def test_a_record_that_stood_unexamined_says_nothing(cfg: Config) -> None:
    from maniac.orchestration.context import redirect_lines

    black = RepoSource(name="black", target="psf/black", is_local=False)

    assert (
        redirect_lines(repository_of(_FakeProvider(source=black), _inst(), cfg), cfg)
        == []
    )
