"""Tests for `PipxProvider` (ADR-0015 Stage 4): detection and resolution."""

from pathlib import Path

import pytest

from maniac.config import Config
from maniac.models import RepoSource
from maniac.sources.providers import pipx

_METADATA_TEMPLATE = """\
Metadata-Version: 2.4
Name: {name}
Version: {version}
{extra}
"""


@pytest.fixture(autouse=True)
def _clear_pipx_home_candidates_cache() -> None:
    pipx._pipx_home_candidates.cache_clear()


def _make_pipx_venv(
    tmp_path: Path,
    package: str,
    real_name: str,
    *,
    dist_info_name: str | None = None,
    metadata_name: str | None = None,
    version: str = "1.0.0",
    extra: str = "",
) -> tuple[Path, Path]:
    """Build `<tmp>/pipx/venvs/<package>/`, return (bin_path, root)."""
    root = tmp_path / "pipx" / "venvs" / package
    dist_info_name = dist_info_name or f"{package}-{version}.dist-info"
    metadata_name = metadata_name or package
    dist_info = root / "lib" / "python3.12" / "site-packages" / dist_info_name
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        _METADATA_TEMPLATE.format(name=metadata_name, version=version, extra=extra),
        encoding="utf-8",
    )
    real = root / "bin" / real_name
    real.parent.mkdir(parents=True)
    real.touch()
    bin_path = tmp_path / "bin" / real_name
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.symlink_to(real)
    return bin_path, root


def _provider(tmp_path: Path, monkeypatch) -> pipx.PipxProvider:
    monkeypatch.setenv("PIPX_HOME", str(tmp_path / "pipx"))
    return pipx.PipxProvider()


def test_detect_fills_every_field_from_metadata(tmp_path, monkeypatch) -> None:
    bin_path, root = _make_pipx_venv(tmp_path, "howdoi", "howdoi", version="2.0.20")
    provider = _provider(tmp_path, monkeypatch)

    inst = provider.detect(bin_path)

    assert inst is not None
    assert inst.binary == "howdoi"
    assert inst.bin_path == bin_path
    assert inst.real_path == bin_path.resolve()
    assert inst.provider == "pipx"
    assert inst.package == "howdoi"
    assert inst.version == "2.0.20"
    assert inst.root == root
    assert inst.parent is None


def test_detect_matches_a_dist_info_whose_name_normalizes_differently(
    tmp_path, monkeypatch
) -> None:
    bin_path, _root = _make_pipx_venv(
        tmp_path,
        "tlp-ui",
        "tlpui",
        dist_info_name="tlp_ui-1.10.1.dist-info",
        metadata_name="tlp-ui",
        version="1.10.1",
    )
    provider = _provider(tmp_path, monkeypatch)

    inst = provider.detect(bin_path)

    assert inst is not None
    assert inst.version == "1.10.1"


def test_detect_rejects_a_path_outside_pipx_venvs(tmp_path, monkeypatch) -> None:
    real = tmp_path / "opt" / "somewhere" / "tool"
    real.parent.mkdir(parents=True)
    real.touch()
    bin_path = tmp_path / "bin" / "tool"
    bin_path.parent.mkdir()
    bin_path.symlink_to(real)
    provider = _provider(tmp_path, monkeypatch)

    assert provider.detect(bin_path) is None


def test_pipx_home_candidates_are_cached_by_environment_inputs() -> None:
    inputs = ("/home/tester/pipx", None, "/home/tester")

    assert pipx._pipx_home_candidates(*inputs) == ("/home/tester/pipx",)
    assert pipx._pipx_home_candidates(*inputs) == ("/home/tester/pipx",)
    assert pipx._pipx_home_candidates.cache_info().hits == 1


def test_resolve_source_reads_a_repository_project_url(tmp_path, monkeypatch) -> None:
    bin_path, _root = _make_pipx_venv(
        tmp_path,
        "tlp-ui",
        "tlpui",
        dist_info_name="tlp_ui-1.10.1.dist-info",
        metadata_name="tlp-ui",
        version="1.10.1",
        extra=(
            "Project-URL: Documentation, https://github.com/d4nj1/TLPUI/docs\n"
            "Project-URL: Homepage, https://github.com/d4nj1/TLPUI\n"
            "Project-URL: Repository, https://github.com/d4nj1/TLPUI\n"
        ),
    )
    provider = _provider(tmp_path, monkeypatch)
    inst = provider.detect(bin_path)
    assert inst is not None

    source = provider.resolve_source(inst, config=Config())

    assert source == RepoSource(name="tlpui", target="d4nj1/TLPUI", is_local=False)


def test_resolve_source_prefers_a_repository_label_over_an_earlier_github_link(
    tmp_path, monkeypatch
) -> None:
    """A GitHub-hosted docs mirror listed before the real repository must not
    win just because it comes first -- the `Repository` label wins."""
    bin_path, _root = _make_pipx_venv(
        tmp_path,
        "tlp-ui",
        "tlpui",
        dist_info_name="tlp_ui-1.10.1.dist-info",
        metadata_name="tlp-ui",
        version="1.10.1",
        extra=(
            "Project-URL: Documentation, https://github.com/readthedocs/tlp-ui-docs\n"
            "Project-URL: Repository, https://github.com/d4nj1/TLPUI\n"
        ),
    )
    provider = _provider(tmp_path, monkeypatch)
    inst = provider.detect(bin_path)
    assert inst is not None

    source = provider.resolve_source(inst, config=Config())

    assert source == RepoSource(name="tlpui", target="d4nj1/TLPUI", is_local=False)


def test_resolve_source_returns_none_with_no_github_url(tmp_path, monkeypatch) -> None:
    bin_path, _root = _make_pipx_venv(tmp_path, "howdoi", "howdoi", version="2.0.20")
    provider = _provider(tmp_path, monkeypatch)
    inst = provider.detect(bin_path)
    assert inst is not None

    assert provider.resolve_source(inst, config=Config()) is None


def test_local_docs_finds_manpage_under_install_root(tmp_path, monkeypatch) -> None:
    bin_path, root = _make_pipx_venv(tmp_path, "howdoi", "howdoi")
    provider = _provider(tmp_path, monkeypatch)
    inst = provider.detect(bin_path)
    assert inst is not None
    manpage = root / "howdoi.1"
    manpage.touch()

    assert provider.local_docs(inst) == [manpage]


def test_local_docs_finds_nothing_when_install_root_ships_no_manpage(
    tmp_path, monkeypatch
) -> None:
    bin_path, _root = _make_pipx_venv(tmp_path, "howdoi", "howdoi")
    provider = _provider(tmp_path, monkeypatch)
    inst = provider.detect(bin_path)
    assert inst is not None

    assert provider.local_docs(inst) == []
