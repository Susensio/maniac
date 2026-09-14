"""Tests for `UvProvider` (ADR-0015 Stage 2): detection and resolution."""

import json
from pathlib import Path

from maniac.config import Config
from maniac.sources.providers import uv
from maniac.sources.providers.registry import registry


def _make_uv_tool(tmp_path: Path, tool: str, real_name: str) -> tuple[Path, Path]:
    """Build `<tmp>/.local/share/uv/tools/<tool>/bin/<real_name>`, return (bin_path, root)."""
    root = tmp_path / ".local" / "share" / "uv" / "tools" / tool
    real = root / "bin" / real_name
    real.parent.mkdir(parents=True)
    real.touch()
    bin_path = tmp_path / ".local" / "bin" / real_name
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.symlink_to(real)
    return bin_path, root


def _add_dist_info(
    root: Path,
    dist_info_name: str,
    *,
    direct_url: dict[str, object] | None = None,
    metadata: str | None = None,
) -> Path:
    dist_info = root / "lib" / "python3.12" / "site-packages" / dist_info_name
    dist_info.mkdir(parents=True)
    if direct_url is not None:
        (dist_info / "direct_url.json").write_text(
            json.dumps(direct_url), encoding="utf-8"
        )
    if metadata is not None:
        (dist_info / "METADATA").write_text(metadata, encoding="utf-8")
    return dist_info


def test_detect_fills_every_field_from_the_install_path(tmp_path: Path) -> None:
    bin_path, root = _make_uv_tool(tmp_path, "ruff", "ruff")
    _add_dist_info(root, "ruff-0.5.0.dist-info")
    provider = uv.UvProvider()

    inst = provider.detect(bin_path)

    assert inst is not None
    assert inst.binary == "ruff"
    assert inst.bin_path == bin_path
    assert inst.real_path == bin_path.resolve()
    assert inst.provider == "uv"
    assert inst.package == "ruff"
    assert inst.version == "0.5.0"
    assert inst.root == root
    assert inst.parent is None


def test_detect_normalizes_the_dist_info_name_before_matching(tmp_path: Path) -> None:
    """uv's dist-info folder underscores what the tool directory dashes (or vice versa)."""
    bin_path, root = _make_uv_tool(tmp_path, "typing-extensions", "typing-extensions")
    _add_dist_info(root, "typing_extensions-4.16.0.dist-info")

    inst = uv.UvProvider().detect(bin_path)

    assert inst is not None
    assert inst.version == "4.16.0"


def test_detect_leaves_version_none_when_no_dist_info_matches(tmp_path: Path) -> None:
    """A tool with no matching dist-info (non-Python entrypoint, odd layout) has no
    version MANIAC can read -- left `None` rather than guessed."""
    bin_path, root = _make_uv_tool(tmp_path, "mytool", "mytool")
    _add_dist_info(root, "some-other-dependency-1.0.dist-info")

    inst = uv.UvProvider().detect(bin_path)

    assert inst is not None
    assert inst.version is None


def test_detect_rejects_a_path_outside_uv_tools(tmp_path: Path) -> None:
    real = tmp_path / "opt" / "somewhere" / "tool"
    real.parent.mkdir(parents=True)
    real.touch()
    bin_path = tmp_path / "bin" / "tool"
    bin_path.parent.mkdir()
    bin_path.symlink_to(real)

    assert uv.UvProvider().detect(bin_path) is None


def test_resolve_source_finds_a_local_editable_checkout(tmp_path: Path) -> None:
    bin_path, root = _make_uv_tool(tmp_path, "maniac", "maniac")
    local_checkout = tmp_path / "Projects" / "maniac"
    local_checkout.mkdir(parents=True)
    _add_dist_info(
        root,
        "maniac-0.1.0.dist-info",
        direct_url={"url": f"file://{local_checkout}", "dir_info": {"editable": True}},
    )
    inst = uv.UvProvider().detect(bin_path)
    assert inst is not None

    source = uv.UvProvider().resolve_source(inst, config=Config(), sources=registry)

    assert source is not None
    assert source.is_local
    assert source.local_path == local_checkout
    assert source.target == f"LOCAL:{local_checkout}"


def test_resolve_source_reads_a_published_packages_repository_metadata(
    tmp_path: Path,
) -> None:
    bin_path, root = _make_uv_tool(tmp_path, "ruff", "ruff")
    _add_dist_info(
        root,
        "ruff-0.5.0.dist-info",
        metadata=(
            "Name: ruff\n"
            "Version: 0.5.0\n"
            "Project-URL: Repository, https://github.com/astral-sh/ruff\n"
        ),
    )
    inst = uv.UvProvider().detect(bin_path)
    assert inst is not None

    source = uv.UvProvider().resolve_source(inst, config=Config(), sources=registry)

    assert source is not None
    assert source.target == "astral-sh/ruff"


def test_resolve_source_returns_none_without_repository_metadata(
    tmp_path: Path,
) -> None:
    bin_path, root = _make_uv_tool(tmp_path, "ruff", "ruff")
    _add_dist_info(root, "ruff-0.5.0.dist-info")
    inst = uv.UvProvider().detect(bin_path)
    assert inst is not None

    assert (
        uv.UvProvider().resolve_source(inst, config=Config(), sources=registry) is None
    )


def test_uv_metadata_scans_are_cached_per_install_root(tmp_path: Path) -> None:
    bin_path, root = _make_uv_tool(tmp_path, "tool", "tool")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _add_dist_info(
        root,
        "tool-1.2.3.dist-info",
        direct_url={"url": f"file://{checkout}"},
    )
    uv._installed_version.cache_clear()
    uv._local_editable_dir.cache_clear()

    first = uv.UvProvider().detect(bin_path)
    second = uv.UvProvider().detect(bin_path)
    assert first is not None
    assert second is not None
    assert first.version == second.version == "1.2.3"
    assert (
        uv.UvProvider().resolve_source(first, config=Config(), sources=registry)
        is not None
    )
    assert (
        uv.UvProvider().resolve_source(second, config=Config(), sources=registry)
        is not None
    )

    assert uv._installed_version.cache_info().hits == 1
    assert uv._local_editable_dir.cache_info().hits == 1


def test_local_docs_finds_manpage_under_install_root(tmp_path: Path) -> None:
    bin_path, root = _make_uv_tool(tmp_path, "ruff", "ruff")
    inst = uv.UvProvider().detect(bin_path)
    assert inst is not None
    manpage = root / "ruff.1"
    manpage.touch()

    assert uv.UvProvider().local_docs(inst) == [manpage]


def test_local_docs_finds_nothing_when_install_root_ships_no_manpage(
    tmp_path: Path,
) -> None:
    bin_path, _root = _make_uv_tool(tmp_path, "ruff", "ruff")
    inst = uv.UvProvider().detect(bin_path)
    assert inst is not None

    assert uv.UvProvider().local_docs(inst) == []
