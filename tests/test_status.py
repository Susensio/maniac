"""Tests for `status` (ADR-0013/ADR-0016): decisions only, no Rich-output scraping."""

import io
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

import maniac.cli as cli_module
from maniac import manifest as manifest_module
from maniac.cli import app
from maniac.cli.status import (
    ActionState,
    StatusRow,
    _grouped_for_display,
    _ProgressReporter,
    _render_status,
    _state_for,
    compute_status,
)
from maniac.config import Config
from maniac.manifest import Tier
from maniac.models import Installation

runner = CliRunner()


class _FakeProvider:
    """Minimal `Provider` stand-in with per-test-configurable answers."""

    def __init__(
        self, name: str = "fake", local_docs: list[Path] | None = None
    ) -> None:
        self.name = name
        self._local_docs = local_docs or []

    def detect(self, bin_path: Path) -> Installation | None:
        return None

    def resolve_source(self, inst: Installation) -> None:
        return None

    def local_docs(self, inst: Installation) -> list[Path]:
        return self._local_docs


def _installation(binary: str = "tool", package: str = "tool") -> Installation:
    return Installation(
        binary=binary,
        bin_path=Path(f"/bin/{binary}"),
        real_path=Path(f"/bin/{binary}"),
        provider="fake",
        package=package,
        version="1.2.3",
        root=Path("/root"),
    )


def test_compute_status_no_args_walks_providers_not_the_manpath(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Enumeration is `discovery.enumerate_installations`, never a manpath scan."""
    page = tmp_path / "tool.1"
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation()
    monkeypatch.setattr(
        "maniac.cli.status.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: [(provider, inst)],
    )

    rows = compute_status(config=_config(tmp_path))

    assert rows == [
        StatusRow(
            tool="tool",
            package="tool",
            provider="fake",
            state=ActionState.SHIPS_UNINSTALLED,
        )
    ]


def _config(tmp_path: Path) -> Config:
    return Config(man_dir=tmp_path / "man" / "man1")


def test_compute_status_ships_a_page_not_installed(tmp_path: Path) -> None:
    page = tmp_path / "install_root" / "tool.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation()
    cfg = _config(tmp_path)

    assert _state_for(provider, inst, "tool", cfg) is ActionState.SHIPS_UNINSTALLED


def test_compute_status_no_page_anywhere_when_install_root_is_empty(
    tmp_path: Path,
) -> None:
    provider = _FakeProvider(local_docs=[])
    inst = _installation()
    cfg = _config(tmp_path)

    assert _state_for(provider, inst, "tool", cfg) is ActionState.NO_PAGE


def test_compute_status_no_provider_is_no_page_anywhere(tmp_path: Path) -> None:
    cfg = _config(tmp_path)

    assert _state_for(None, None, "tool", cfg) is ActionState.NO_PAGE


def test_compute_status_maniac_managed_wins_even_with_an_install_root_page(
    tmp_path: Path,
) -> None:
    """A tier-1/2 install copies its page verbatim, no provenance header --
    checking the manifest first (ADR-0017) is what still recognises it.
    """
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1"
    installed.write_text(".TH TOOL 1\n", encoding="utf-8")
    manifest_module.record(
        "tool", installed, Tier.INSTALL_ROOT, "src", "abc123", config=cfg
    )

    page = tmp_path / "install_root" / "tool.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    provider = _FakeProvider(local_docs=[page])
    inst = _installation()

    assert _state_for(provider, inst, "tool", cfg) is ActionState.MANAGED


def test_compute_status_managed_page_can_be_compressed(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    installed = cfg.man_dir / "tool.1.gz"
    installed.write_bytes(b"\x1f\x8b")
    manifest_module.record(
        "tool", installed, Tier.INSTALL_ROOT, "src", "abc123", config=cfg
    )

    assert _state_for(None, None, "tool", cfg) is ActionState.MANAGED


def test_compute_status_unmanaged_page_in_man_dir_is_not_managed(
    tmp_path: Path,
) -> None:
    """ADR-0017's stated correction: a page a user hand-placed in `man_dir`,
    absent from the manifest, is no longer reported as MANIAC-managed."""
    cfg = _config(tmp_path)
    cfg.man_dir.mkdir(parents=True)
    (cfg.man_dir / "tool.1").write_text(".TH TOOL 1\n", encoding="utf-8")

    assert _state_for(None, None, "tool", cfg) is ActionState.NO_PAGE


def test_compute_status_manifest_entry_with_vanished_file_is_not_managed(
    tmp_path: Path,
) -> None:
    """A manifest entry recorded before a crash between record and copy (ADR-0017)
    is detected rather than blindly reported MANAGED."""
    cfg = _config(tmp_path)
    manifest_module.record(
        "tool", cfg.man_dir / "tool.1", Tier.SYNTHESIS, "model", "abc123", config=cfg
    )

    assert _state_for(None, None, "tool", cfg) is ActionState.NO_PAGE


def test_compute_status_with_tools_is_unfiltered_and_resolves_each_by_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = _FakeProvider(local_docs=[])
    inst = _installation(binary="bash")
    monkeypatch.setattr(
        "maniac.cli.status.discovery.find_installation",
        lambda name, bin_dir=None: (provider, inst) if name == "bash" else None,
    )

    rows = compute_status(["bash", "unknown"], config=_config(tmp_path))

    assert [row.tool for row in rows] == ["bash", "unknown"]
    assert rows[0].state is ActionState.NO_PAGE  # empty local_docs, no managed page
    assert rows[1].state is ActionState.NO_PAGE  # no provider at all
    assert rows[1].package == "unknown"
    assert rows[1].provider == ""


def test_compute_status_named_tools_are_deduplicated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "maniac.cli.status.discovery.find_installation", lambda name, bin_dir=None: None
    )

    rows = compute_status(["uv", "uv"], config=_config(tmp_path))

    assert [row.tool for row in rows] == ["uv"]


def test_compute_status_on_row_callbacks_are_optional_and_no_op_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every caller besides the CLI (including every other test here) omits
    the callbacks and must see unchanged behaviour."""
    monkeypatch.setattr(
        "maniac.cli.status.discovery.find_installation", lambda name, bin_dir=None: None
    )

    rows = compute_status(["uv"], config=_config(tmp_path))

    assert [row.tool for row in rows] == ["uv"]


def test_compute_status_named_tools_report_row_progress_but_no_discovery_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The `tools` path never calls `discovery.enumerate_installations`, so
    its discovery callbacks must never fire."""
    monkeypatch.setattr(
        "maniac.cli.status.discovery.find_installation", lambda name, bin_dir=None: None
    )
    row_starts: list[int] = []
    row_scans = 0
    discovery_starts: list[int] = []

    def on_row_start(total: int) -> None:
        row_starts.append(total)

    def on_row_scan() -> None:
        nonlocal row_scans
        row_scans += 1

    compute_status(
        ["uv", "gh"],
        config=_config(tmp_path),
        on_discovery_start=discovery_starts.append,
        on_row_start=on_row_start,
        on_row_scan=on_row_scan,
    )

    assert row_starts == [2]
    assert row_scans == 2
    assert discovery_starts == []


def test_compute_status_no_args_threads_both_phases_callbacks(
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
        "maniac.cli.status.discovery.enumerate_installations", fake_enumerate
    )

    def on_row_start(total: int) -> None:
        row_starts.append(total)

    def on_row_scan() -> None:
        nonlocal row_scans
        row_scans += 1

    compute_status(
        config=_config(tmp_path),
        on_discovery_start=discovery_starts.append,
        on_discovery_scan=lambda: None,
        on_row_start=on_row_start,
        on_row_scan=on_row_scan,
    )

    assert discovery_starts == [5]
    assert row_starts == [1]
    assert row_scans == 1


def test_progress_reporter_advances_total_and_completed_across_both_phases() -> None:
    """`_ProgressReporter` is `status()`'s single combined bar: each phase's
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


def test_render_status_tty_shows_the_two_column_table() -> None:
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, no_color=True)

    _render_status(
        test_console,
        [
            StatusRow(
                tool="pandoc",
                package="pandoc",
                provider="mise",
                state=ActionState.SHIPS_UNINSTALLED,
            )
        ],
    )

    output = buf.getvalue()
    assert "Manpage Status" in output
    assert "pandoc" in output
    assert ActionState.SHIPS_UNINSTALLED.value in output
    assert "install it" not in output


def test_render_status_colors_the_state_column_per_category() -> None:
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, color_system="standard")

    _render_status(
        test_console,
        [
            StatusRow(
                tool="pandoc",
                package="pandoc",
                provider="mise",
                state=ActionState.SHIPS_UNINSTALLED,
            ),
            StatusRow(
                tool="gum", package="gum", provider="mise", state=ActionState.NO_PAGE
            ),
            StatusRow(
                tool="tmux", package="tmux", provider="mise", state=ActionState.MANAGED
            ),
        ],
    )

    output = buf.getvalue()
    assert "\x1b[33mavailable\x1b[0m" in output
    assert "\x1b[31mmissing\x1b[0m" in output
    assert "\x1b[32mmanaged\x1b[0m" in output
    assert "zero" not in output


def test_render_status_collapses_siblings_sharing_a_package_and_state_by_count() -> (
    None
):
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, no_color=True, width=200)

    rows = [
        StatusRow(
            tool=name,
            package="pandoc",
            provider="mise",
            state=ActionState.SHIPS_UNINSTALLED,
        )
        for name in ("pandoc", "pandoc-lua", "pandoc-server")
    ]
    _render_status(test_console, rows)

    lines = [line for line in buf.getvalue().splitlines() if "pandoc" in line]
    assert len(lines) == 1
    assert "pandoc (3 binaries)" in lines[0]
    assert "pandoc-lua" not in lines[0]
    assert "pandoc-server" not in lines[0]


def test_grouped_for_display_solo_tool_keeps_its_own_name() -> None:
    rows = [
        StatusRow(
            tool="pandoc",
            package="pandoc",
            provider="mise",
            state=ActionState.SHIPS_UNINSTALLED,
        )
    ]

    assert _grouped_for_display(rows) == [("pandoc", ActionState.SHIPS_UNINSTALLED)]


def test_grouped_for_display_many_siblings_render_as_package_and_count() -> None:
    """A package exposing many binaries under one state must not blow up the label."""
    rows = [
        StatusRow(
            tool=name,
            package="python",
            provider="mise",
            state=ActionState.MANAGED,
        )
        for name in ("python3", "pip", "pydoc", "idle", "2to3")
    ]

    assert _grouped_for_display(rows) == [("python (5 binaries)", ActionState.MANAGED)]


def test_render_status_does_not_collapse_siblings_in_different_states() -> None:
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, no_color=True, width=200)

    rows = [
        StatusRow(
            tool="pandoc",
            package="pandoc",
            provider="mise",
            state=ActionState.SHIPS_UNINSTALLED,
        ),
        StatusRow(
            tool="pandoc-lua",
            package="pandoc",
            provider="mise",
            state=ActionState.MANAGED,
        ),
    ]
    _render_status(test_console, rows)

    lines = [line for line in buf.getvalue().splitlines() if "pandoc" in line]
    assert len(lines) == 2


def test_render_status_non_tty_prints_bare_names(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The path `xargs maniac install` relies on."""
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=False)

    _render_status(
        test_console,
        [
            StatusRow(
                tool="gum", package="gum", provider="mise", state=ActionState.NO_PAGE
            ),
            StatusRow(
                tool="gh", package="gh", provider="mise", state=ActionState.NO_PAGE
            ),
        ],
    )

    assert capsys.readouterr().out == "gum\ngh\n"
    assert buf.getvalue() == ""


def test_render_status_bare_names_never_collapse_by_package(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`xargs maniac install` needs binaries, one per line, never a package label."""
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=False)

    rows = [
        StatusRow(
            tool=name,
            package="pandoc",
            provider="mise",
            state=ActionState.SHIPS_UNINSTALLED,
        )
        for name in ("pandoc", "pandoc-lua", "pandoc-server")
    ]
    _render_status(test_console, rows)

    assert capsys.readouterr().out == "pandoc\npandoc-lua\npandoc-server\n"


def test_render_status_names_forces_bare_output_on_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True)

    _render_status(
        test_console,
        [
            StatusRow(
                tool="gum", package="gum", provider="mise", state=ActionState.NO_PAGE
            )
        ],
        names=True,
    )

    assert capsys.readouterr().out == "gum\n"
    assert buf.getvalue() == ""


def test_cli_status_pipe_emits_bare_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Smoke test: `status` through the full CLI, piped, is bare names -- nothing else."""
    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=False, no_color=True)
    )
    monkeypatch.setattr("maniac.cli.status.default_cfg", _config(tmp_path))
    monkeypatch.setattr(
        "maniac.cli.status.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: [
            (_FakeProvider(), _installation(binary="gum"))
        ],
    )

    res = runner.invoke(app, ["status"])
    assert res.exit_code == 0
    assert res.output == "gum\n"


def test_cli_status_tty_shows_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli_module.console, "_instance", Console(force_terminal=True, no_color=True)
    )
    monkeypatch.setattr("maniac.cli.status.default_cfg", _config(tmp_path))
    monkeypatch.setattr(
        "maniac.cli.status.discovery.enumerate_installations",
        lambda on_start=None, on_scan=None: [
            (_FakeProvider(), _installation(binary="gum"))
        ],
    )

    res = runner.invoke(app, ["status"])
    assert res.exit_code == 0
    assert "Manpage Status" in res.output
