import io
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from rich.console import Console
from rich.text import Text
from typer.testing import CliRunner

import maniac.cli as cli_module
from maniac import manifest
from maniac.cli import _render_eval_table, _repo_cell, app
from maniac.config import Config
from maniac.installer import UninstallRefused, UninstallResult
from maniac.models import DocFile, EvaluationResult, RepoSource
from maniac.orchestration.context import ResolvedTool

runner = CliRunner()


@pytest.fixture(autouse=True)
def _plain_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the CLI's lazy console to a colourless one for this module.

    H1: `Console()` picks up ambient `FORCE_COLOR`, injecting ANSI codes that
    split plain-substring assertions. Tests must not depend on the shell's
    environment, so pin it here rather than changing production colour
    behaviour.

    No width pin: commands now compute a result structure that tests assert
    against directly (see `compute_eval`, `compute_compare`, `compute_uninstall`),
    so no remaining assertion depends on how Rich wraps a long dynamic value
    such as a path. A handful of rendering smoke tests below only check
    short, fixed strings that cannot wrap at any terminal width.
    """
    monkeypatch.setattr(
        cli_module.console,
        "_instance",
        Console(force_terminal=False, no_color=True),
    )


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Maniac:" in result.output
    assert "source" in result.output
    assert "install" in result.output
    assert "eval" in result.output
    assert "list" in result.output


def test_importing_cli_does_not_construct_config() -> None:
    script = textwrap.dedent(
        """
        import maniac.config

        class FailingConfig:
            def __init__(self):
                raise AssertionError("CLI import constructed Config")

        maniac.config.Config = FailingConfig
        import maniac.cli
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


def test_cli_constructs_and_threads_one_config(monkeypatch: pytest.MonkeyPatch) -> None:
    constructed: list[Config] = []

    class CountingConfig(Config):
        def __init__(self) -> None:
            super().__init__()
            constructed.append(self)

    observed: list[Config] = []
    monkeypatch.setattr(cli_module, "Config", CountingConfig)
    monkeypatch.setattr(
        "maniac.sources.crawler.find_subcommands",
        lambda cmd, **kwargs: (
            observed.append(kwargs["config"]) or {"> tool --help": "tool help"}
        ),
    )

    result = runner.invoke(app, ["source", "crawl", "tool"])

    assert result.exit_code == 0
    assert len(constructed) == 1
    assert observed == constructed


def test_repo_cell_links_known_repo_and_labels_unknown() -> None:
    known = _repo_cell(
        RepoSource(name="rg", target="BurntSushi/ripgrep", is_local=False)
    )
    unknown = _repo_cell(RepoSource(name="rg", target="rg", is_local=False))

    assert known.plain == "BurntSushi/ripgrep"
    assert len(known.spans) == 1
    assert known.spans[0].start == 0
    assert known.spans[0].end == len(known)
    assert known.spans[0].style.link == "https://github.com/BurntSushi/ripgrep"
    assert unknown.plain == "Unknown"


def test_repo_cell_aqua_backend_prefix_links_to_the_real_repo() -> None:
    """M-repo-cell-aqua: a Mise `tool_alias` naming aqua leaks its prefix into `target`.

    The link must point at the real GitHub repo, not
    `https://github.com/aqua:sharkdp/pastel` -- the backend prefix
    concatenated straight into the URL.
    """
    cell = _repo_cell(
        RepoSource(name="pastel", target="aqua:sharkdp/pastel", is_local=False)
    )

    assert cell.plain == "aqua:sharkdp/pastel"
    assert len(cell.spans) == 1
    assert cell.spans[0].style.link == "https://github.com/sharkdp/pastel"


def test_repo_cell_link_does_not_include_table_padding() -> None:
    from rich.console import Console
    from rich.table import Table

    short_target = "a/b"
    table = Table()
    table.add_column("Repository")
    table.add_row(_repo_cell(RepoSource(name="a", target=short_target, is_local=False)))
    table.add_row(
        _repo_cell(
            RepoSource(
                name="long",
                target="organization/repository",
                is_local=False,
            )
        )
    )

    segments = list(Console(width=40, force_terminal=True).render(table))
    target_index = next(
        index for index, segment in enumerate(segments) if segment.text == short_target
    )

    target_style = segments[target_index].style
    assert target_style is not None
    assert target_style.link == "https://github.com/a/b"
    assert segments[target_index + 1].text.isspace()
    padding_style = segments[target_index + 1].style
    assert padding_style is None or padding_style.link is None


def test_repo_cell_local_source_links_the_path_without_the_prefix(
    tmp_path: Path,
) -> None:
    """The stored `LOCAL:` prefix is provenance, not something a reader needs."""
    cell = _repo_cell(
        RepoSource(
            name="yadm",
            target=f"LOCAL:{tmp_path}",
            is_local=True,
            local_path=tmp_path,
        )
    )

    assert cell.plain == str(tmp_path)
    assert "LOCAL:" not in cell.plain
    assert len(cell.spans) == 1
    assert cell.spans[0].start == 0
    assert cell.spans[0].end == len(cell)
    assert cell.spans[0].style.link == tmp_path.as_uri()


def test_repo_cell_local_source_abbreviates_home_but_links_the_real_path() -> None:
    path = Path.home() / "Projects" / "claude2agents"
    cell = _repo_cell(
        RepoSource(
            name="claude2agents",
            target=f"LOCAL:{path}",
            is_local=True,
            local_path=path,
        )
    )

    assert cell.plain == "~/Projects/claude2agents"
    assert cell.spans[0].style.link == path.as_uri()


def test_repo_cell_local_source_stays_distinct_without_colour() -> None:
    """Colour is gone in a pipe, under NO_COLOR and for a colourblind reader.

    The leading `~` carries the distinction on its own; italic repeats it in
    a second non-hue channel wherever styling survives.
    """
    path = Path.home() / "Projects" / "claude2agents"
    local = _repo_cell(
        RepoSource(name="c2a", target=f"LOCAL:{path}", is_local=True, local_path=path)
    )
    remote = _repo_cell(
        RepoSource(name="rg", target="BurntSushi/ripgrep", is_local=False)
    )

    assert local.spans[0].style.italic
    console = Console(file=io.StringIO(), force_terminal=True, no_color=True, width=60)

    def plain_render(cell: Text) -> str:
        return "".join(
            segment.text for segment in console.render(cell) if segment.text.strip()
        )

    assert plain_render(local).startswith("~/")
    assert not plain_render(remote).startswith(("~", "/"))


def test_repo_cell_non_github_backend_prefix_renders_as_plain_text() -> None:
    """npm/pipx/cargo package names have no fixed relationship to a GitHub path."""
    cell = _repo_cell(RepoSource(name="eslint", target="npm:eslint", is_local=False))

    assert cell.plain == "npm:eslint"
    assert cell.style == "yellow"


def test_cli_source_crawl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "maniac.sources.crawler.find_subcommands",
        lambda cmd, **kwargs: {"> git --help": "git help content"},
    )
    result = runner.invoke(app, ["source", "crawl", "git"])
    assert result.exit_code == 0
    assert "> git --help" in result.output
    assert "git help content" in result.output


def test_cli_source_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_source = RepoSource(name="tmux", target="tmux/tmux-builds", is_local=False)
    monkeypatch.setattr(
        "maniac.sources.resolution.discover_repo",
        lambda tool, **kwargs: raw_source,
    )
    observed: list[RepoSource] = []
    monkeypatch.setattr(
        "maniac.sources.docs.fetch_and_extract_docs",
        lambda source, cache_dir, **kwargs: (
            observed.append(source)
            or ([DocFile(rel_path="README.md", content="Content")], False)
        ),
    )
    result = runner.invoke(app, ["source", "docs", "mytool"])
    assert result.exit_code == 0
    assert "Discovered repository source" in result.output
    assert "README.md" in result.output
    assert raw_source.target == "tmux/tmux-builds"
    assert observed == [RepoSource(name="tmux", target="tmux/tmux", is_local=False)]


def test_cli_install_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "maniac.sources.loginpath.which_login",
        lambda name: Path(f"/bin/{name}"),
    )
    monkeypatch.setattr(
        "maniac.orchestration.pipeline.find_subcommands",
        lambda cmd, **kwargs: {"> tool --help": "Usage: tool"},
    )
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )
    monkeypatch.setattr(cli_module, "Config", lambda: Config(output_dir=tmp_path))

    result = runner.invoke(app, ["install", "mytool", "--dry-run"])
    assert result.exit_code == 0
    assert "synthesized from --help" in result.output


def test_cli_install_exits_nonzero_when_synthesis_produces_no_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0048: pandoc missing/rejecting the markdown must not read as success.

    `compile_to_man` returns `False` rather than raising (`generation/compiler.py`),
    so `synthesize` reaches tier 3 with `installed_path=None` and a
    `Tier.SYNTHESIS` outcome -- the fourth no-page path the old
    `outcome.tier is None` check missed entirely.
    """
    from maniac.models import PipelineResult

    monkeypatch.setattr(
        "maniac.sources.loginpath.which_login",
        lambda name: Path(f"/bin/{name}"),
    )

    def _synthesize(tool: ResolvedTool, **kwargs: object) -> PipelineResult:
        return PipelineResult(
            tool_name=tool.tool_name,
            repo_source=RepoSource(
                name=tool.tool_name, target="org/repo", is_local=False
            ),
            command_count=1,
            doc_file_count=0,
            context_path=None,
            markdown_path=tmp_path / f"{tool.tool_name}.1.md",
            roff_path=None,
            installed_path=None,
            markdown_content="# doc",
        )

    monkeypatch.setattr("maniac.orchestration.pipeline.synthesize", _synthesize)

    result = runner.invoke(app, ["install", "mytool"])
    assert result.exit_code != 0
    assert "1/1 tool(s) did not install" in result.output


def test_cli_install_reuses_config_for_existing_destination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An existing destination must not make installation create another Config.

    A foreign page at the destination is refused by the top-of-`run_install`
    precheck (ADR item 1) before any tier -- and so before `install_manpage`
    or `manifest.transaction` are ever reached -- so this now tracks the
    `Config` instance through `manifest.load`, the precheck's own read,
    rather than through a transaction that no longer opens.
    """
    from maniac.models import Installation

    constructed: list[Config] = []

    class TrackingConfig(Config):
        def __init__(self) -> None:
            super().__init__(
                config_dir=tmp_path / "config",
                man_dir=tmp_path / "man1",
                manifest_path=tmp_path / "installed.json",
            )
            constructed.append(self)

    page = tmp_path / "source" / "mytool.1"
    page.parent.mkdir()
    page.write_text(".TH MYTOOL 1", encoding="utf-8")
    destination = tmp_path / "man1" / "mytool.1"
    destination.parent.mkdir()
    destination.write_text(".TH MYTOOL 1 vendor", encoding="utf-8")
    installation = Installation(
        binary="mytool",
        bin_path=tmp_path / "bin" / "mytool",
        real_path=tmp_path / "bin" / "mytool",
        provider="test",
        package="mytool",
        version="1.0",
        root=tmp_path / "source",
    )

    class Provider:
        def local_docs(self, inst: Installation) -> list[Path]:
            assert inst is installation
            return [page]

    observed: list[Config | None] = []

    monkeypatch.setattr(cli_module, "Config", TrackingConfig)
    monkeypatch.setattr(
        "maniac.orchestration.install.resolve_bin_path",
        lambda tool, bin_dir=None: Path(f"/bin/{tool}"),
    )
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda tool, bin_dir=None: (Provider(), installation),
    )
    monkeypatch.setattr("maniac.manifest.Config", TrackingConfig)
    real_load = manifest.load

    def load(config: Config | None = None) -> dict[str, manifest.Entry]:
        observed.append(config)
        return real_load(config)

    monkeypatch.setattr("maniac.orchestration.install.manifest.load", load)

    result = runner.invoke(app, ["install", "mytool"])

    assert result.exit_code != 0
    assert "foreign or vendor manpage already exists" in result.output
    assert len(constructed) == 1
    assert observed == constructed


def test_cli_install_exits_nonzero_for_unreachable_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0048: ADR-0020's unreachable-binary refusal still produces no page."""
    monkeypatch.setattr("maniac.sources.loginpath.which_login", lambda name: None)
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )

    result = runner.invoke(app, ["install", "project-local-tool"])

    assert result.exit_code != 0
    assert "login shell" in result.output


def test_cli_install_exits_nonzero_when_no_synthesize_finds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0048: `--no-synthesize` finding nothing at tiers 1-2 is still no page."""
    monkeypatch.setattr(
        "maniac.sources.loginpath.which_login",
        lambda name: Path(f"/bin/{name}"),
    )
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )

    result = runner.invoke(
        app, ["install", "nonexistent_unknown_tool_xyz", "--no-synthesize"]
    )

    assert result.exit_code != 0
    assert "no install-root or repository page found" in result.output


def test_cli_install_dry_run_exits_nonzero_when_no_tier_would_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0048's correction: a dry run reports the verdict it reached, not
    just the fact that it wrote nothing -- finding no tier to preview is
    still no page, the same as a real run finding none."""
    monkeypatch.setattr(
        "maniac.sources.loginpath.which_login",
        lambda name: Path(f"/bin/{name}"),
    )
    monkeypatch.setattr(
        "maniac.orchestration.context.resolution.find_installation",
        lambda name, bin_dir=None: None,
    )

    result = runner.invoke(
        app,
        ["install", "nonexistent_unknown_tool_xyz", "--no-synthesize", "--dry-run"],
    )

    assert result.exit_code != 0
    assert "no install-root or repository page found" in result.output


def test_cli_install_always_installs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`install` has no `--no-install`: the command's own name is the answer."""
    from maniac.models import PipelineResult

    monkeypatch.setattr(
        "maniac.sources.loginpath.which_login",
        lambda name: Path(f"/bin/{name}"),
    )
    observed: dict[str, object] = {}

    def _synthesize(tool: ResolvedTool, **kwargs: object) -> PipelineResult:
        observed["install"] = kwargs["install"]
        return PipelineResult(
            tool_name=tool.tool_name,
            repo_source=RepoSource(
                name=tool.tool_name, target="org/repo", is_local=False
            ),
            command_count=1,
            doc_file_count=1,
            context_path=None,
            markdown_path=tmp_path / f"{tool.tool_name}.1.md",
            roff_path=tmp_path / f"{tool.tool_name}.1",
            installed_path=tmp_path / f"{tool.tool_name}.1",
            markdown_content="# doc",
        )

    monkeypatch.setattr("maniac.orchestration.pipeline.synthesize", _synthesize)

    res = runner.invoke(app, ["install", "mytool"])
    assert res.exit_code == 0
    assert observed == {"install": True}


def test_cli_install_rejects_the_removed_no_install_flag() -> None:
    """M-inverted-install: `--no-install` contradicted the command's own verb; dropped outright."""
    res = runner.invoke(app, ["install", "mytool", "--no-install"])
    assert res.exit_code == 2


def test_cli_install_zero_tools_exits_quietly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`$(maniac status)` can legitimately expand to nothing."""
    called = False

    def _synthesize(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("maniac.orchestration.pipeline.synthesize", _synthesize)

    res = runner.invoke(app, ["install"])
    assert res.exit_code == 0
    assert res.output == ""
    assert not called


def test_fish_completion_includes_only_dry_run() -> None:
    result = runner.invoke(
        app,
        [],
        prog_name="maniac",
        env={
            "_MANIAC_COMPLETE": "complete_fish",
            "_TYPER_COMPLETE_FISH_ACTION": "get-args",
            "_TYPER_COMPLETE_ARGS": "maniac install --dry",
        },
    )

    assert result.exit_code == 0
    assert (
        result.output
        == "--dry-run\tPreview without installing or generating anything.\n"
    )

    result = runner.invoke(
        app,
        [],
        prog_name="maniac",
        env={
            "_MANIAC_COMPLETE": "complete_fish",
            "_TYPER_COMPLETE_FISH_ACTION": "get-args",
            "_TYPER_COMPLETE_ARGS": "maniac install --no-dry",
        },
    )

    assert result.exit_code == 0
    assert not result.output.strip()


def test_cli_verbose_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    result = runner.invoke(app, ["-v", "--help"])
    assert result.exit_code == 0
    assert "Maniac:" in result.output

    monkeypatch.setenv("MANIAC_VERBOSE", "1")
    result_env = runner.invoke(app, ["--help"])
    assert result_env.exit_code == 0


def test_render_eval_table() -> None:
    output_buf = io.StringIO()
    test_console = Console(file=output_buf, color_system=None, force_terminal=False)
    result = EvaluationResult(
        score=85,
        passed=True,
        rubric_breakdown={
            "domain_ontology": 17,
            "correctness_coverage": 18,
            "formatting": 18,
            "subsystem_grouping": 16,
            "environment_reference_examples": 16,
        },
        defects=["Minor defect note"],
        summary="Evaluation summary.",
    )
    _render_eval_table(test_console, "sampletool", result)
    output = output_buf.getvalue()
    assert "Quality Evaluation: sampletool (85/100)" in output
    assert "Domain Ontology & Architecture" in output
    assert "PASSED" in output
    assert "Evaluation summary." in output
    assert "Minor defect note" in output


def test_cli_uninstall(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from maniac.cli.uninstall import compute_uninstall

    monkeypatch.setattr(
        "maniac.installer.uninstall_manpage",
        lambda tool, purge, config: UninstallResult(removed=[tmp_path / f"{tool}.1"]),
    )
    outcome = compute_uninstall("mytool")
    assert outcome.result == UninstallResult(removed=[tmp_path / "mytool.1"])

    res = runner.invoke(app, ["uninstall", "mytool"])
    assert res.exit_code == 0
    assert "Uninstalled manpage for mytool!" in res.output


def test_cli_uninstall_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    from maniac.cli.uninstall import compute_uninstall

    monkeypatch.setattr(
        "maniac.installer.uninstall_manpage",
        lambda tool, purge, config: UninstallResult(),
    )
    outcome = compute_uninstall("nonexistent")
    assert outcome.result == UninstallResult()

    res = runner.invoke(app, ["uninstall", "nonexistent"])
    assert res.exit_code == 0
    assert "No installed manpage found for 'nonexistent'" in res.output


def test_cli_uninstall_refused_companion(monkeypatch: pytest.MonkeyPatch) -> None:
    """A companion request (ADR-0053) renders yellow, not red, and still
    exits non-zero (ADR-0048's reasoning for a deliberate refusal)."""

    def _raise(tool: str, purge: bool, config: object) -> UninstallResult:
        raise UninstallRefused(
            f"'{tool}' is part of 'eza's installation (bundled in the same "
            f"release, ADR-0042) -- run 'maniac uninstall eza' to remove "
            f"the whole group."
        )

    monkeypatch.setattr("maniac.installer.uninstall_manpage", _raise)

    res = runner.invoke(app, ["uninstall", "eza_colors"])
    assert res.exit_code == 1
    assert "eza_colors" in res.output
    assert "maniac uninstall eza" in res.output
    assert "Error uninstalling" not in res.output


def test_cli_uninstall_foreign_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """M2: a foreign page left in `man_dir` must not be reported as fully uninstalled.

    Asserts on the returned structure rather than rendered text -- the
    foreign path is an arbitrary-length pytest tmp_path, and Rich would wrap
    it unpredictably depending on the ambient terminal width.
    """
    from maniac.cli.uninstall import compute_uninstall

    foreign_path = tmp_path / "man1" / "mytool.1"
    monkeypatch.setattr(
        "maniac.installer.uninstall_manpage",
        lambda tool, purge, config: UninstallResult(
            removed=[tmp_path / f"{tool}.1"], foreign_kept=foreign_path
        ),
    )
    outcome = compute_uninstall("mytool")
    assert outcome.result.removed == [tmp_path / "mytool.1"]
    assert outcome.result.foreign_kept == foreign_path

    res = runner.invoke(app, ["uninstall", "mytool"])
    assert res.exit_code == 0
    assert "Uninstalled manpage for mytool!" in res.output
    assert "Left non-MANIAC manpage in place" in res.output


def test_cli_uninstall_modified_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A retargeted or dangling link renders a message distinct from
    `foreign_kept`'s, and must not claim the bytes changed -- nothing here
    was edited, the link itself no longer points where install left it."""
    from maniac.cli.uninstall import compute_uninstall

    modified_path = tmp_path / "man1" / "mytool.1"
    monkeypatch.setattr(
        "maniac.installer.uninstall_manpage",
        lambda tool, purge, config: UninstallResult(modified_kept=[modified_path]),
    )
    outcome = compute_uninstall("mytool")
    assert outcome.result.foreign_kept is None
    assert outcome.result.modified_kept == [modified_path]

    res = runner.invoke(app, ["uninstall", "mytool"])
    assert res.exit_code == 0
    assert "no longer points where" in res.output
    assert "bytes have changed" not in res.output
    assert "non-MANIAC" not in res.output


def test_cli_uninstall_changed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A page removed despite a bytes mismatch renders a warning, not a refusal.

    `changed=[changed_path]` with an empty `removed` is the production shape
    for a targetless entry with a vendor backup: `_remove_recorded_manpage`
    restores the backup instead of appending to `removed_paths`, so
    `removed` stays empty while `changed` still names the page
    (`installer.py`). `restored=[changed_path]` is what now keeps that shape
    out of the "not found" guard (F1) -- `changed` alone no longer does, so
    a stub carrying `changed` without `restored` (or `removed`) is not the
    production shape and would wrongly hit the guard.
    """
    from maniac.cli.uninstall import compute_uninstall

    changed_path = tmp_path / "man1" / "mytool.1"
    monkeypatch.setattr(
        "maniac.installer.uninstall_manpage",
        lambda tool, purge, config: UninstallResult(
            changed=[changed_path], restored=[changed_path]
        ),
    )
    outcome = compute_uninstall("mytool")
    assert outcome.result.changed == [changed_path]
    assert outcome.result.removed == []

    res = runner.invoke(app, ["uninstall", "mytool"])
    assert res.exit_code == 0
    assert "No installed manpage found" not in res.output
    assert "bytes had changed since install" in res.output


def test_cli_uninstall_restored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """F1: a bytes-matching entry with a vendor backup must not read as
    'nothing happened' -- `removed`, `changed` and `modified_kept` are all
    empty for this shape, so `restored` is the only field the guard can
    check, and the render path needs a line for it too.
    """
    from maniac.cli.uninstall import compute_uninstall

    restored_path = tmp_path / "man1" / "mytool.1"
    monkeypatch.setattr(
        "maniac.installer.uninstall_manpage",
        lambda tool, purge, config: UninstallResult(restored=[restored_path]),
    )
    outcome = compute_uninstall("mytool")
    assert outcome.result.restored == [restored_path]
    assert outcome.result.removed == []
    assert outcome.result.changed == []

    res = runner.invoke(app, ["uninstall", "mytool"])
    assert res.exit_code == 0
    assert "No installed manpage found" not in res.output
    assert "Uninstalled manpage for mytool!" in res.output


def test_cli_install_multiple_all_fail_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M10: `install` must not silently exit 0 when every tool fails."""
    from maniac.exceptions import ManiacError

    def _raise(*args: object, **kwargs: object) -> None:
        raise ManiacError("boom")

    monkeypatch.setattr(
        "maniac.sources.loginpath.which_login",
        lambda name: Path(f"/bin/{name}"),
    )
    monkeypatch.setattr("maniac.orchestration.pipeline.synthesize", _raise)
    res = runner.invoke(app, ["install", "toolone", "tooltwo"])
    assert res.exit_code == 1
    assert "Install failed for toolone: boom" in res.output
    assert "Install failed for tooltwo: boom" in res.output
    assert "2/2 tool(s) did not install" in res.output


def test_cli_install_multiple_partial_success_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """M10: one failure among several tools still fails the multi-install."""
    from maniac.exceptions import ManiacError
    from maniac.models import PipelineResult

    monkeypatch.setattr(
        "maniac.sources.loginpath.which_login",
        lambda name: Path(f"/bin/{name}"),
    )

    def _synthesize(tool: ResolvedTool, **kwargs: object) -> PipelineResult:
        if tool.tool_name == "badtool":
            raise ManiacError("boom")
        return PipelineResult(
            tool_name=tool.tool_name,
            repo_source=RepoSource(
                name=tool.tool_name, target="org/repo", is_local=False
            ),
            command_count=1,
            doc_file_count=1,
            context_path=None,
            markdown_path=tmp_path / f"{tool.tool_name}.1.md",
            roff_path=tmp_path / f"{tool.tool_name}.1",
            installed_path=tmp_path / f"{tool.tool_name}.1",
            markdown_content="# doc",
        )

    monkeypatch.setattr("maniac.orchestration.pipeline.synthesize", _synthesize)
    res = runner.invoke(app, ["install", "goodtool", "badtool"])
    assert res.exit_code == 1
    assert "1/2 tool(s) did not install" in res.output


def test_render_install_does_not_swallow_bracketed_detail() -> None:
    """M-bracket-escape: `[no synthesis]` is literal text, not Rich markup.

    Rich reads an unescaped `[...]` as a style tag and silently drops it,
    so an un-escaped render would print "pandoc   upstream manpage from
    install root (3.10.2)" with the `[no synthesis]` suffix missing.
    """
    import io

    from rich.console import Console

    from maniac.cli.install import _render_install
    from maniac.orchestration.install import InstallOutcome, Tier

    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=False, no_color=True)
    outcome = InstallOutcome(
        tool="pandoc",
        tier=Tier.INSTALL_ROOT,
        detail="upstream manpage from install root (3.10.2)   [no synthesis]",
        installed_path=Path("/usr/share/man/man1/pandoc.1"),
    )

    _render_install(test_console, outcome, dry_run=False)

    assert "[no synthesis]" in buf.getvalue()


def test_cli_uninstall_rejects_removed_force_option() -> None:
    """`uninstall --force` is gone: the flag never bypassed anything real,
    and `--force` bypassing `changed` would let a stale install shadow a
    page whose bytes moved on for an unrelated reason."""
    res = runner.invoke(app, ["uninstall", "mytool", "--force"])
    assert res.exit_code != 0
