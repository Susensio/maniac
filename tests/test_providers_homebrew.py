"""Tests for `HomebrewProvider` (ADR-0015 Stage 4): detection and resolution.

Unverified: Homebrew is not installed on the development system. `brew info`
is mocked at the module boundary (`homebrew._brew_homepage`), mirroring how
`test_providers_go.py` mocks `go._read_module_info`.
"""

from pathlib import Path

from maniac.config import Config
from maniac.models import RepoSource
from maniac.sources.providers import homebrew
from maniac.sources.providers.registry import registry


def _make_cellar_install(
    tmp_path: Path, package: str, version: str, real_name: str
) -> Path:
    """Build `<tmp>/Cellar/<package>/<version>/bin/<real_name>` and a symlink
    at `<tmp>/bin/<real_name>`, mirroring mise's own symlink shape."""
    real = tmp_path / "Cellar" / package / version / "bin" / real_name
    real.parent.mkdir(parents=True)
    real.touch()
    bin_path = tmp_path / "bin" / real_name
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.symlink_to(real)
    return bin_path


def test_detect_fills_every_field_from_the_cellar_path(tmp_path: Path) -> None:
    bin_path = _make_cellar_install(tmp_path, "jq", "1.7.1", "jq")
    provider = homebrew.HomebrewProvider()

    inst = provider.detect(bin_path)

    assert inst is not None
    assert inst.binary == "jq"
    assert inst.bin_path == bin_path
    assert inst.real_path == bin_path.resolve()
    assert inst.provider == "homebrew"
    assert inst.package == "jq"
    assert inst.version == "1.7.1"
    assert inst.root == tmp_path / "Cellar" / "jq" / "1.7.1"
    assert inst.parent is None


def test_detect_rejects_a_path_outside_cellar(tmp_path: Path) -> None:
    real = tmp_path / "opt" / "somewhere" / "tool"
    real.parent.mkdir(parents=True)
    real.touch()
    bin_path = tmp_path / "bin" / "tool"
    bin_path.parent.mkdir()
    bin_path.symlink_to(real)

    assert homebrew.HomebrewProvider().detect(bin_path) is None


def test_resolve_source_reads_a_github_homepage(tmp_path, monkeypatch) -> None:
    bin_path = _make_cellar_install(tmp_path, "jq", "1.7.1", "jq")
    inst = homebrew.HomebrewProvider().detect(bin_path)
    assert inst is not None
    monkeypatch.setattr(
        homebrew, "_brew_homepage", lambda package: "https://github.com/jqlang/jq"
    )

    source = homebrew.HomebrewProvider().resolve_source(
        inst, config=Config(), sources=registry
    )

    assert source == RepoSource(name="jq", target="jqlang/jq", is_local=False)


def test_resolve_source_returns_none_for_a_non_github_homepage(
    tmp_path, monkeypatch
) -> None:
    bin_path = _make_cellar_install(tmp_path, "jq", "1.7.1", "jq")
    inst = homebrew.HomebrewProvider().detect(bin_path)
    assert inst is not None
    monkeypatch.setattr(
        homebrew, "_brew_homepage", lambda package: "https://jqlang.org"
    )

    assert (
        homebrew.HomebrewProvider().resolve_source(
            inst, config=Config(), sources=registry
        )
        is None
    )


def test_resolve_source_returns_none_when_brew_info_fails(
    tmp_path, monkeypatch
) -> None:
    bin_path = _make_cellar_install(tmp_path, "jq", "1.7.1", "jq")
    inst = homebrew.HomebrewProvider().detect(bin_path)
    assert inst is not None
    monkeypatch.setattr(homebrew, "_brew_homepage", lambda package: None)

    assert (
        homebrew.HomebrewProvider().resolve_source(
            inst, config=Config(), sources=registry
        )
        is None
    )


def test_local_docs_finds_manpage_under_install_root(tmp_path: Path) -> None:
    bin_path = _make_cellar_install(tmp_path, "jq", "1.7.1", "jq")
    inst = homebrew.HomebrewProvider().detect(bin_path)
    assert inst is not None
    manpage = inst.root / "share" / "man" / "man1" / "jq.1"
    manpage.parent.mkdir(parents=True)
    manpage.touch()

    assert homebrew.HomebrewProvider().local_docs(inst) == [manpage]


def test_local_docs_finds_nothing_when_install_root_ships_no_manpage(
    tmp_path: Path,
) -> None:
    bin_path = _make_cellar_install(tmp_path, "jq", "1.7.1", "jq")
    inst = homebrew.HomebrewProvider().detect(bin_path)
    assert inst is not None

    assert homebrew.HomebrewProvider().local_docs(inst) == []
