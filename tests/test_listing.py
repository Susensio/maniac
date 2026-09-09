"""Tests for `list` (ADR-0018): decisions only, no Rich-output scraping."""

import io
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

import maniac.cli as cli_module
from maniac import manifest as manifest_module
from maniac.cli import app
from maniac.cli.listing import (
    ActionState,
    PageSource,
    ToolRow,
    _classify,
    _filter_rows,
    _grouped_for_display,
    _ProgressReporter,
    _render_list,
    _resolve_upstream,
    compute_rows,
)
from maniac.config import Config
from maniac.manifest import Tier
from maniac.models import Installation, RepoSource

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_real_man(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test stays off the development machine's real `man` database
    (mirrors `tests/test_compare.py:216`'s monkeypatch of the same function);
    a test that needs `man` to resolve something overrides this itself.
    """
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: None,
    )


class _FakeProvider:
    """Minimal `Provider` stand-in with per-test-configurable answers."""

    def __init__(
        self,
        name: str = "fake",
        local_docs: list[Path] | None = None,
        source: RepoSource | None = None,
    ) -> None:
        self.name = name
        self._local_docs = local_docs or []
        self._source = source

    def detect(self, bin_path: Path) -> Installation | None:
        return None

    def resolve_source(self, inst: Installation) -> RepoSource | None:
        return self._source

    def local_docs(self, inst: Installation) -> list[Path]:
        return self._local_docs


def _installation(
    binary: str = "tool",
    package: str = "tool",
    version: str | None = "1.2.3",
    root: Path = Path("/root"),
) -> Installation:
    return Installation(
        binary=binary,
        bin_path=Path(f"/bin/{binary}"),
        real_path=Path(f"/bin/{binary}"),
        provider="fake",
        package=package,
        version=version,
        root=root,
    )


def _config(tmp_path: Path) -> Config:
    return Config(man_dir=tmp_path / "man" / "man1")


# -- compute_rows: enumeration and named-tools paths ------------------------


def test_compute_rows_no_args_walks_providers_not_the_manpath(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Enumeration is `discovery.enumerate_installations`; `man` decides state, not the manpath scan."""
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation(root=tmp_path)
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: [(provider, inst)],
    )

    rows = compute_rows(config=_config(tmp_path))

    assert rows == [
        ToolRow(
            tool="tool",
            package="tool",
            provider="fake",
            state=ActionState.AVAILABLE,
            source=PageSource.INSTALL_ROOT,
            upstream=None,
        )
    ]


def test_compute_rows_with_tools_is_unfiltered_and_resolves_each_by_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = _FakeProvider(local_docs=[])
    inst = _installation(binary="bash")
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.find_installation",
        lambda name, bin_dir=None: (provider, inst) if name == "bash" else None,
    )

    rows = compute_rows(["bash", "unknown"], config=_config(tmp_path))

    assert [row.tool for row in rows] == ["bash", "unknown"]
    assert rows[0].state is ActionState.MISSING  # empty local_docs, nothing to resolve
    assert rows[1].state is ActionState.MISSING  # no provider at all
    assert rows[1].package == "unknown"
    assert rows[1].provider == ""


def test_compute_rows_named_tools_are_deduplicated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.find_installation",
        lambda name, bin_dir=None: None,
    )

    rows = compute_rows(["uv", "uv"], config=_config(tmp_path))

    assert [row.tool for row in rows] == ["uv"]


def test_compute_rows_on_row_callbacks_are_optional_and_no_op_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every caller besides the CLI (including every other test here) omits
    the callbacks and must see unchanged behaviour."""
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.find_installation",
        lambda name, bin_dir=None: None,
    )

    rows = compute_rows(["uv"], config=_config(tmp_path))

    assert [row.tool for row in rows] == ["uv"]


def test_compute_rows_named_tools_report_row_progress_but_no_discovery_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The `tools` path never calls `discovery.enumerate_installations`, so
    its discovery callbacks must never fire."""
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.find_installation",
        lambda name, bin_dir=None: None,
    )
    row_starts: list[int] = []
    row_scans = 0
    discovery_starts: list[int] = []

    def on_row_start(total: int) -> None:
        row_starts.append(total)

    def on_row_scan() -> None:
        nonlocal row_scans
        row_scans += 1

    compute_rows(
        ["uv", "gh"],
        config=_config(tmp_path),
        on_discovery_start=discovery_starts.append,
        on_row_start=on_row_start,
        on_row_scan=on_row_scan,
    )

    assert row_starts == [2]
    assert row_scans == 2
    assert discovery_starts == []


def test_compute_rows_no_args_threads_both_phases_callbacks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = _FakeProvider()
    inst = _installation()
    discovery_starts: list[int] = []
    row_starts: list[int] = []
    row_scans = 0

    def fake_enumerate(on_start=None, on_scan=None):
        if on_start is not None:
            on_start(5)
        if on_scan is not None:
            on_scan()
        return [(provider, inst)]

    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations", fake_enumerate
    )

    def on_row_start(total: int) -> None:
        row_starts.append(total)

    def on_row_scan() -> None:
        nonlocal row_scans
        row_scans += 1

    compute_rows(
        config=_config(tmp_path),
        on_discovery_start=discovery_starts.append,
        on_discovery_scan=lambda: None,
        on_row_start=on_row_start,
        on_row_scan=on_row_scan,
    )

    assert discovery_starts == [5]
    assert row_starts == [1]
    assert row_scans == 1


# -- _classify: the four states ----------------------------------------------


def test_classify_available_when_install_root_ships_an_uninstalled_page(
    tmp_path: Path,
) -> None:
    page = tmp_path / "install_root" / "tool.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation()
    cfg = _config(tmp_path)

    assert _classify(provider, inst, "tool", cfg) == (
        ActionState.AVAILABLE,
        PageSource.INSTALL_ROOT,
    )


def test_classify_missing_when_nothing_resolves_and_install_root_is_empty(
    tmp_path: Path,
) -> None:
    provider = _FakeProvider(local_docs=[])
    inst = _installation()
    cfg = _config(tmp_path)

    assert _classify(provider, inst, "tool", cfg) == (
        ActionState.MISSING,
        PageSource.NONE,
    )


def test_classify_missing_when_no_provider_at_all(tmp_path: Path) -> None:
    cfg = _config(tmp_path)

    assert _classify(None, None, "tool", cfg) == (ActionState.MISSING, PageSource.NONE)


def test_classify_ok_when_man_resolves_and_nothing_suggests_staleness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    installed = tmp_path / "usr" / "man1" / "tool.1"
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )

    assert _classify(None, None, "tool", cfg) == (ActionState.OK, PageSource.SYSTEM)


# -- _classify: Source classification for a reachable page ------------------


def test_classify_source_is_maniac_when_manifest_owns_the_resolved_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    manifest_module.record(
        "tool", installed, Tier.INSTALL_ROOT, "src", "abc123", config=cfg
    )
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )

    assert _classify(None, None, "tool", cfg) == (ActionState.OK, PageSource.MANIAC)


def test_classify_source_is_install_root_when_resolved_page_sits_under_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    root = tmp_path / "install_root"
    installed = root / "share" / "man" / "man1" / "tool.1"
    installed.parent.mkdir(parents=True)
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    inst = _installation(root=root)

    assert _classify(None, inst, "tool", cfg) == (
        ActionState.OK,
        PageSource.INSTALL_ROOT,
    )


def test_classify_source_is_system_when_resolved_page_is_unowned_and_outside_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    installed = tmp_path / "usr" / "share" / "man" / "man1" / "tool.1.gz"
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    inst = _installation(root=tmp_path / "install_root")

    assert _classify(None, inst, "tool", cfg) == (ActionState.OK, PageSource.SYSTEM)


# -- _classify: same-page matching (compression, symlink) -------------------


def test_classify_managed_page_can_be_compressed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`man -w` may report a compressed path where the manifest recorded an
    uncompressed one; ownership must still be recognized."""
    cfg = _config(tmp_path)
    entry_dir = tmp_path / "man"
    entry_dir.mkdir(parents=True)
    entry_path = entry_dir / "tool.1"
    entry_path.write_text(".TH TOOL 1\n", encoding="utf-8")
    manifest_module.record(
        "tool", entry_path, Tier.INSTALL_ROOT, "src", "abc123", config=cfg
    )
    installed = entry_dir / "tool.1.gz"  # same base page, compressed
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )

    assert _classify(None, None, "tool", cfg) == (ActionState.OK, PageSource.MANIAC)


def test_classify_managed_page_matched_through_a_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Either side of the comparison may be a symlink; both are resolved first."""
    cfg = _config(tmp_path)
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real_page = real_dir / "tool.1"
    real_page.write_text(".TH TOOL 1\n", encoding="utf-8")
    manifest_module.record(
        "tool", real_page, Tier.INSTALL_ROOT, "src", "abc123", config=cfg
    )

    link_dir = tmp_path / "man" / "man1"
    link_dir.mkdir(parents=True)
    linked_page = link_dir / "tool.1"
    linked_page.symlink_to(real_page)
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: linked_page,
    )

    assert _classify(None, None, "tool", cfg) == (ActionState.OK, PageSource.MANIAC)


# -- _classify: outdated requires positive evidence --------------------------


def test_classify_outdated_when_recorded_version_differs_from_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    manifest_module.record(
        "tool",
        installed,
        Tier.SYNTHESIS,
        "model",
        "abc123",
        config=cfg,
        version="1.0.0",
    )
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    inst = _installation(version="2.0.0")

    assert _classify(None, inst, "tool", cfg) == (
        ActionState.OUTDATED,
        PageSource.MANIAC,
    )


def test_classify_ok_when_entry_records_no_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`local_lib` never reports a version; absent evidence reads `ok`, permanently."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    manifest_module.record(
        "tool", installed, Tier.SYNTHESIS, "model", "abc123", config=cfg, version=None
    )
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    inst = _installation(version="2.0.0")

    assert _classify(None, inst, "tool", cfg) == (ActionState.OK, PageSource.MANIAC)


def test_classify_ok_when_installation_version_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    manifest_module.record(
        "tool",
        installed,
        Tier.SYNTHESIS,
        "model",
        "abc123",
        config=cfg,
        version="1.0.0",
    )
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    inst = _installation(version=None)

    assert _classify(None, inst, "tool", cfg) == (ActionState.OK, PageSource.MANIAC)


def test_classify_ok_when_page_is_unowned_even_with_a_version_mismatch_in_hand(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`outdated` requires ownership too, not only a version fact somewhere."""
    cfg = _config(tmp_path)
    installed = tmp_path / "usr" / "man1" / "tool.1"
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )
    inst = _installation(version="2.0.0")

    assert _classify(None, inst, "tool", cfg) == (ActionState.OK, PageSource.SYSTEM)


# -- _classify: reversal of ADR-0016's manifest-only rule --------------------


def test_classify_unmanaged_page_hand_placed_in_man_dir_reads_ok_system(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0017's correction still holds -- an unrecorded page in `man_dir`
    is not MANIAC-owned -- but `man` resolving it now reads `ok`, not `missing`."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "maniac.cli.listing.find_installed_manpage_path",
        lambda man_bin, tool_name: installed,
    )

    assert _classify(None, None, "tool", cfg) == (ActionState.OK, PageSource.SYSTEM)


def test_classify_manifest_entry_with_vanished_file_and_no_man_hit_is_missing(
    tmp_path: Path,
) -> None:
    """A manifest entry recorded before a crash between record and copy (ADR-0017)
    is not enough on its own once `man` is also asked."""
    cfg = _config(tmp_path)
    manifest_module.record(
        "tool", cfg.man_dir / "tool.1", Tier.SYNTHESIS, "model", "abc123", config=cfg
    )

    assert _classify(None, None, "tool", cfg) == (ActionState.MISSING, PageSource.NONE)


def test_classify_managed_file_present_but_unreachable_by_man_is_not_managed(
    tmp_path: Path,
) -> None:
    """The real behaviour change ADR-0018 makes: a manifest entry whose file
    exists is no longer enough to read `managed`/`ok`/MANIAC -- `man` must
    also resolve it. Absent that, the row falls through to `available` or
    `missing` like any other unreachable page."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    entry_path = cfg.man_dir / "tool.1"
    entry_path.write_text(".TH TOOL 1\n", encoding="utf-8")
    manifest_module.record(
        "tool", entry_path, Tier.INSTALL_ROOT, "src", "abc123", config=cfg
    )
    # find_installed_manpage_path stays patched to None by the autouse fixture:
    # `man` does not resolve this tool at all, despite the manifest entry.

    assert _classify(None, None, "tool", cfg) == (ActionState.MISSING, PageSource.NONE)

    provider = _FakeProvider(local_docs=[tmp_path / "install_root" / "tool.1"])
    inst = _installation()
    assert _classify(provider, inst, "tool", cfg) == (
        ActionState.AVAILABLE,
        PageSource.INSTALL_ROOT,
    )


# -- _resolve_upstream: offline-only, mise gated out -------------------------


def test_resolve_upstream_calls_provider_resolve_source() -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    provider = _FakeProvider(source=source)
    inst = _installation()

    assert _resolve_upstream(provider, inst) is source


def test_resolve_upstream_gates_mise_out_without_calling_resolve_source() -> None:
    """`MiseProvider.resolve_source` can fall back to a network fetch of the
    Mise registry archive on a cache miss; this pass must make no network
    call, so the mise provider is excluded here rather than risking it."""

    class _ExplodingMiseProvider(_FakeProvider):
        def resolve_source(self, inst: Installation) -> RepoSource | None:
            raise AssertionError("resolve_source must not be called for mise")

    provider = _ExplodingMiseProvider(name="mise")
    inst = _installation()

    assert _resolve_upstream(provider, inst) is None


def test_resolve_upstream_none_without_provider_or_installation() -> None:
    assert _resolve_upstream(None, None) is None


# -- _filter_rows: union within an axis, intersection across axes -----------


def _row(
    tool: str, state: ActionState, source: PageSource = PageSource.NONE
) -> ToolRow:
    return ToolRow(
        tool=tool,
        package=tool,
        provider="fake",
        state=state,
        source=source,
        upstream=None,
    )


def test_filter_rows_no_flags_is_unfiltered() -> None:
    rows = [_row("a", ActionState.OK), _row("b", ActionState.MISSING)]

    assert (
        _filter_rows(
            rows, outdated=False, available=False, missing=False, managed=False
        )
        == rows
    )


def test_filter_rows_unions_within_the_state_axis() -> None:
    rows = [
        _row("ok", ActionState.OK),
        _row("avail", ActionState.AVAILABLE),
        _row("miss", ActionState.MISSING),
        _row("stale", ActionState.OUTDATED),
    ]

    filtered = _filter_rows(
        rows, outdated=False, available=True, missing=True, managed=False
    )

    assert [row.tool for row in filtered] == ["avail", "miss"]


def test_filter_rows_intersects_across_axes() -> None:
    rows = [
        _row("stale-managed", ActionState.OUTDATED, PageSource.MANIAC),
        _row("stale-unmanaged", ActionState.OUTDATED, PageSource.SYSTEM),
        _row("ok-managed", ActionState.OK, PageSource.MANIAC),
    ]

    filtered = _filter_rows(
        rows, outdated=True, available=False, missing=False, managed=True
    )

    assert [row.tool for row in filtered] == ["stale-managed"]


# -- CLI: filtering happens before the render branch -------------------------


def test_cli_list_pipe_emits_exactly_the_filtered_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=False, no_color=True)
    )
    monkeypatch.setattr("maniac.cli.listing.default_cfg", _config(tmp_path))

    available_page = tmp_path / "install_root" / "gum.1"
    available_page.parent.mkdir(parents=True)
    available_page.write_text(".TH GUM 1\n", encoding="utf-8")
    available_provider = _FakeProvider(local_docs=[available_page])
    missing_provider = _FakeProvider(local_docs=[])

    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: [
            (available_provider, _installation(binary="gum")),
            (missing_provider, _installation(binary="ghost")),
        ],
    )

    res = runner.invoke(app, ["list", "--available"])
    assert res.exit_code == 0
    assert res.output == "gum\n"


# -- _grouped_for_display -----------------------------------------------------


def test_grouped_for_display_solo_tool_keeps_its_own_name() -> None:
    rows = [
        ToolRow(
            tool="pandoc",
            package="pandoc",
            provider="mise",
            state=ActionState.AVAILABLE,
            source=PageSource.INSTALL_ROOT,
            upstream=None,
        )
    ]

    label, row = _grouped_for_display(rows)[0]
    assert label == "pandoc"
    assert row.state is ActionState.AVAILABLE


def test_grouped_for_display_many_siblings_render_as_package_and_count() -> None:
    """A package exposing many binaries under one state must not blow up the label."""
    rows = [
        ToolRow(
            tool=name,
            package="python",
            provider="mise",
            state=ActionState.OK,
            source=PageSource.MANIAC,
            upstream=None,
        )
        for name in ("python3", "pip", "pydoc", "idle", "2to3")
    ]

    grouped = _grouped_for_display(rows)
    assert len(grouped) == 1
    assert grouped[0][0] == "python (5 binaries)"


def test_grouped_for_display_key_includes_source_and_upstream() -> None:
    """Two rows sharing (provider, package, state) but differing in Source or
    Upstream must never collapse -- the extended key ADR-0018 requires."""
    rows = [
        ToolRow(
            tool="pandoc",
            package="pandoc",
            provider="mise",
            state=ActionState.OK,
            source=PageSource.MANIAC,
            upstream=None,
        ),
        ToolRow(
            tool="pandoc-lua",
            package="pandoc",
            provider="mise",
            state=ActionState.OK,
            source=PageSource.SYSTEM,
            upstream=None,
        ),
    ]

    assert len(_grouped_for_display(rows)) == 2


# -- Rendering -----------------------------------------------------------------


def test_render_list_tty_shows_the_four_column_table() -> None:
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, no_color=True)

    _render_list(
        test_console,
        [
            ToolRow(
                tool="pandoc",
                package="pandoc",
                provider="mise",
                state=ActionState.AVAILABLE,
                source=PageSource.INSTALL_ROOT,
                upstream=None,
            )
        ],
    )

    output = buf.getvalue()
    assert "Manpage Reachability" in output
    assert "pandoc" in output
    assert ActionState.AVAILABLE.value in output
    assert PageSource.INSTALL_ROOT.value in output


def test_render_list_colors_the_state_column_per_category() -> None:
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, color_system="standard")

    _render_list(
        test_console,
        [
            ToolRow(
                tool="pandoc",
                package="pandoc",
                provider="mise",
                state=ActionState.AVAILABLE,
                source=PageSource.INSTALL_ROOT,
                upstream=None,
            ),
            ToolRow(
                tool="gum",
                package="gum",
                provider="mise",
                state=ActionState.MISSING,
                source=PageSource.NONE,
                upstream=None,
            ),
            ToolRow(
                tool="tmux",
                package="tmux",
                provider="mise",
                state=ActionState.OK,
                source=PageSource.MANIAC,
                upstream=None,
            ),
            ToolRow(
                tool="rg",
                package="rg",
                provider="mise",
                state=ActionState.OUTDATED,
                source=PageSource.MANIAC,
                upstream=None,
            ),
        ],
    )

    output = buf.getvalue()
    assert "\x1b[33mavailable\x1b[0m" in output
    assert "\x1b[31mmissing\x1b[0m" in output
    assert "\x1b[32mok\x1b[0m" in output
    assert "\x1b[33moutdated\x1b[0m" in output


def test_render_list_upstream_column_blank_when_unresolvable() -> None:
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, no_color=True)

    _render_list(
        test_console,
        [
            ToolRow(
                tool="gum",
                package="gum",
                provider="mise",
                state=ActionState.MISSING,
                source=PageSource.NONE,
                upstream=None,
            )
        ],
    )

    assert "Unknown" not in buf.getvalue()


def test_render_list_non_tty_prints_bare_names(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The path `xargs maniac install` relies on."""
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=False)

    _render_list(
        test_console,
        [
            ToolRow(
                tool="gum",
                package="gum",
                provider="mise",
                state=ActionState.MISSING,
                source=PageSource.NONE,
                upstream=None,
            ),
            ToolRow(
                tool="gh",
                package="gh",
                provider="mise",
                state=ActionState.MISSING,
                source=PageSource.NONE,
                upstream=None,
            ),
        ],
    )

    assert capsys.readouterr().out == "gum\ngh\n"
    assert buf.getvalue() == ""


def test_render_list_names_forces_bare_output_on_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True)

    _render_list(
        test_console,
        [
            ToolRow(
                tool="gum",
                package="gum",
                provider="mise",
                state=ActionState.MISSING,
                source=PageSource.NONE,
                upstream=None,
            )
        ],
        names=True,
    )

    assert capsys.readouterr().out == "gum\n"
    assert buf.getvalue() == ""


# -- Progress reporter (unchanged behaviour, ported) -------------------------


def test_progress_reporter_advances_total_and_completed_across_both_phases() -> None:
    """`_ProgressReporter` is `list`'s single combined bar: each phase's
    `on_phase_start` extends one shared total, each `on_scan` advances one
    shared completed count."""
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, no_color=True)
    reporter = _ProgressReporter(test_console)

    reporter.on_phase_start(3)  # discovery phase: 3 $PATH candidates
    reporter.on_scan()
    reporter.on_scan()
    reporter.on_scan()
    reporter.on_phase_start(2)  # row phase: 2 rows found by discovery
    reporter.on_scan()
    reporter.on_scan()

    task = reporter._progress.tasks[0]
    assert task.total == 5
    assert task.completed == 5

    reporter.stop()
    assert reporter._progress.live.is_started is False


# -- CLI smoke tests ----------------------------------------------------------


def test_cli_list_pipe_emits_bare_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Smoke test: `list` through the full CLI, piped, is bare names -- nothing else."""
    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=False, no_color=True)
    )
    monkeypatch.setattr("maniac.cli.listing.default_cfg", _config(tmp_path))
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: [
            (_FakeProvider(), _installation(binary="gum"))
        ],
    )

    res = runner.invoke(app, ["list"])
    assert res.exit_code == 0
    assert res.output == "gum\n"


def test_cli_list_tty_shows_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=True, no_color=True)
    )
    monkeypatch.setattr("maniac.cli.listing.default_cfg", _config(tmp_path))
    monkeypatch.setattr(
        "maniac.cli.listing.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: [
            (_FakeProvider(), _installation(binary="gum"))
        ],
    )

    res = runner.invoke(app, ["list"])
    assert res.exit_code == 0
    assert "Manpage Reachability" in res.output
