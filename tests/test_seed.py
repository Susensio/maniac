"""Which older, human-written page generation starts from (`candidates.seed_for`).

First found wins: the page maniac installed before from the tool or its
repository, then a page `man` shows that dpkg proves is the same software at
another version, then the repository's current page. Never one maniac
generated or help2man made, and never one only a shared name ties to the tool.
"""

from pathlib import Path

import pytest

from maniac.config import Config
from maniac.manifest import PROVENANCE_SIGNATURE, Entry, Tier
from maniac.models import RemoteRepoSource
from maniac.sources.lookup import candidates
from maniac.sources.packages import ExternalPageFreshness, ExternalPageVerification

from .listing_support import _FakeProvider, _installation
from .manifest_support import record_entry

_HUMAN = (
    '.TH TOOL 1 "2024-01-01" "tool 1.0.0" "User Commands"\n.SH NAME\ntool \\- does\n'
)
_REPO = RemoteRepoSource.from_identifier("tool", "https://github.com/acme/tool")


def _page(path: Path, content: str = _HUMAN) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        man_dir=tmp_path / "man" / "man1",
        output_dir=tmp_path / "out",
        manifest_path=tmp_path / "state" / "installed.json",
    )


@pytest.fixture
def nothing_found(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """No page from `man`, none upstream; records what was asked upstream."""
    asked: list[str] = []
    monkeypatch.setattr(candidates, "find_installed_manpage_path", lambda man, t: None)

    def discover(source, binary, **kwargs):
        asked.append(binary)
        assert kwargs["version"] is None
        return [], True

    monkeypatch.setattr(candidates, "discover_repo_manpages", discover)
    return asked


def _seed(cfg: Config, *, version: str | None = "2.0.0") -> candidates.Seed | None:
    return candidates.seed_for(
        "tool",
        _FakeProvider(),
        _installation(version=version),
        lambda: _REPO,
        config=cfg,
    )


def _recorded(cfg: Config, tmp_path: Path, tier: Tier, content: str = _HUMAN) -> None:
    target = _page(cfg.output_dir / "tool.1", content)
    record_entry(
        "tool",
        Entry(
            path=cfg.man_dir / "tool.1",
            tier=tier,
            source="x",
            checksum="c",
            version="1.0.0",
            target=target,
        ),
        config=cfg,
    )


def test_the_page_maniac_installed_before_comes_first(
    cfg: Config, tmp_path: Path, nothing_found: list[str]
) -> None:
    _recorded(cfg, tmp_path, Tier.REPOSITORY)

    seed = _seed(cfg)

    assert seed is not None
    assert seed.label == "the page maniac installed before (1.0.0)"
    assert nothing_found == []


def test_a_page_maniac_generated_is_never_a_seed(
    cfg: Config, tmp_path: Path, nothing_found: list[str]
) -> None:
    """ADR-0065: a previous generation is no seed for the next."""
    _recorded(cfg, tmp_path, Tier.SYNTHESIS)

    assert _seed(cfg) is None
    assert nothing_found == ["tool"]


def test_a_page_dpkg_proves_is_the_same_software_at_another_version(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = _page(tmp_path / "usr" / "share" / "man" / "man1" / "tool.1")
    monkeypatch.setattr(candidates, "find_installed_manpage_path", lambda m, t: shown)
    monkeypatch.setattr(
        candidates,
        "verify_external_page",
        lambda page, **_: ExternalPageVerification(
            ExternalPageFreshness.MISMATCH, "tool-pkg", "1.4.0"
        ),
    )

    seed = _seed(cfg)

    assert seed is not None
    assert (seed.path, seed.label) == (shown, "the tool-pkg package's page (1.4.0)")


@pytest.mark.parametrize(
    "freshness", [ExternalPageFreshness.UNVERIFIED, ExternalPageFreshness.MATCH]
)
def test_a_page_not_proven_the_same_software_at_another_version_is_passed_over(
    cfg: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    nothing_found: list[str],
    freshness: ExternalPageFreshness,
) -> None:
    """UNVERIFIED: only the name ties it to the tool. MATCH: the same version
    would have been installed as a page, not used as a seed."""
    shown = _page(tmp_path / "usr" / "share" / "man" / "man1" / "tool.1")
    monkeypatch.setattr(candidates, "find_installed_manpage_path", lambda m, t: shown)
    monkeypatch.setattr(
        candidates,
        "verify_external_page",
        lambda page, **_: ExternalPageVerification(freshness, None),
    )

    assert _seed(cfg) is None


def test_the_repositorys_current_page_comes_last(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _page(tmp_path / "cache" / "tool.1")
    monkeypatch.setattr(candidates, "find_installed_manpage_path", lambda m, t: None)
    monkeypatch.setattr(
        candidates, "discover_repo_manpages", lambda *a, **kw: ([upstream], True)
    )

    seed = _seed(cfg)

    assert seed is not None
    assert seed.label == f"the current page in {_REPO.identity} (1.0.0)"


@pytest.mark.parametrize(
    "content",
    [
        f"{PROVENANCE_SIGNATURE}\n{_HUMAN}",
        '.\\" DO NOT MODIFY THIS FILE!  It was generated by help2man 1.49.3.\n'
        + _HUMAN,
        '.TH OTHER 1 "2024" "other 1.0" "User Commands"\n',
    ],
    ids=["maniac", "help2man", "another-title"],
)
def test_only_pages_people_wrote_for_this_tool_qualify(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    upstream = _page(tmp_path / "cache" / "tool.1", content)
    monkeypatch.setattr(candidates, "find_installed_manpage_path", lambda m, t: None)
    monkeypatch.setattr(
        candidates, "discover_repo_manpages", lambda *a, **kw: ([upstream], True)
    )

    assert _seed(cfg) is None


def test_an_unclaimed_binary_has_only_its_previous_page(
    cfg: Config, nothing_found: list[str]
) -> None:
    seed = candidates.seed_for("tool", None, None, lambda: _REPO, config=cfg)

    assert seed is None
    assert nothing_found == []


def test_a_long_page_is_cut_to_the_seed_limit(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _page(tmp_path / "cache" / "tool.1", _HUMAN + "x\n" * 30_000)
    monkeypatch.setattr(candidates, "find_installed_manpage_path", lambda m, t: None)
    monkeypatch.setattr(
        candidates, "discover_repo_manpages", lambda *a, **kw: ([upstream], True)
    )

    seed = _seed(cfg)

    assert seed is not None
    assert len(seed.content) == candidates.SEED_MAX_CHARS
