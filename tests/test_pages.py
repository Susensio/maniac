"""`list` and `update` over the pages maniac installed (CONTRACT.md rules 1 and 4)."""

import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from maniac.cli import app
from maniac.config import Config
from maniac.listing.models import ActionState, PageSource
from maniac.listing.pages import managed_pages
from maniac.manifest import Entry, Tier
from maniac.orchestration.install import InstallOutcome

from .listing_support import _FakeProvider, _installation
from .manifest_support import record_entry

runner = CliRunner()


def _config(tmp_path: Path) -> Config:
    return Config(
        man_dir=tmp_path / "man" / "man1",
        output_dir=tmp_path / "out",
        manifest_path=tmp_path / "state" / "installed.json",
    )


def _page(
    cfg: Config,
    tool: str,
    *,
    version: str | None = None,
    group: str | None = None,
    binary: Path | None = None,
) -> Entry:
    """A recorded page whose manpath link is sound."""
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    cfg.man_dir.mkdir(parents=True, exist_ok=True)
    target = cfg.output_dir / f"{tool}.1"
    target.write_text(".TH X 1\n", encoding="utf-8")
    link = cfg.man_dir / f"{tool}.1"
    link.symlink_to(target)
    entry = Entry(
        path=link,
        tier=Tier.SYNTHESIS,
        source="model",
        checksum="c",
        version=version,
        target=target,
        group=group,
        binary=binary,
    )
    record_entry(tool, entry, config=cfg)
    return entry


def _claimed(monkeypatch: pytest.MonkeyPatch, versions: dict[str, str | None]) -> None:
    """`find_installation` answers each named tool at the given version."""
    provider = _FakeProvider()
    monkeypatch.setattr(
        "maniac.listing.inventory.resolution.find_installation",
        lambda name, bin_dir=None, **_: (
            (provider, _installation(binary=name, version=versions[name]))
            if name in versions
            else None
        ),
    )


def _script(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho '{version}'\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_each_page_compares_what_it_documents_with_what_is_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    _page(cfg, "same", version="1.0.0")
    _page(cfg, "older", version="1.0.0")
    _page(cfg, "unrecorded")
    _claimed(monkeypatch, {"same": "1.0.0", "older": "2.0.0", "unrecorded": "3.0.0"})

    rows, unmanaged = managed_pages(cfg)

    assert unmanaged == []
    assert [(r.tool, r.state, r.documented, r.installed) for r in rows] == [
        ("older", ActionState.OUTDATED, "1.0.0", "2.0.0"),
        ("same", ActionState.OK, "1.0.0", "1.0.0"),
        ("unrecorded", ActionState.UNKNOWN, None, "3.0.0"),
    ]
    assert rows[2].note == "no version was recorded for this page"
    assert {r.source for r in rows} == {PageSource.MANIAC}


def test_companions_answer_to_their_primary_and_unmanaged_names_come_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    _page(cfg, "git", version="2.0", group="git")
    _page(cfg, "git-config", version="2.0", group="git")
    _claimed(monkeypatch, {"git": "2.0"})

    every, _ = managed_pages(cfg)
    named, unmanaged = managed_pages(cfg, ["git", "git-config", "rg"])

    assert [r.tool for r in every] == ["git"]
    assert [r.tool for r in named] == ["git"]
    assert unmanaged == ["git-config", "rg"]


def test_a_pinned_page_is_checked_on_its_own_copy_and_unknown_once_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A page `--force` pinned to a copy (CONTRACT.md rule 2) answers to that
    copy, whatever the login `$PATH` reaches; when it is gone, `unknown`."""
    copy = _script(tmp_path / "proj" / ".venv" / "bin" / "tool", "tool 3.1.0")
    cfg = _config(tmp_path)
    _page(cfg, "tool", version="tool 3.0.0", binary=copy)
    _claimed(monkeypatch, {"tool": "9.9.9"})

    [row], _ = managed_pages(cfg)
    assert (row.state, row.installed, row.copy) == (
        ActionState.OUTDATED,
        "tool 3.1.0",
        copy,
    )

    copy.unlink()
    [row], _ = managed_pages(cfg)
    assert row.state is ActionState.UNKNOWN
    assert row.note == f"the copy it documents, {copy}, is gone"


def test_a_page_whose_link_was_replaced_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    entry = _page(cfg, "tool", version="1.0")
    entry.path.unlink()
    entry.path.write_text(".TH OTHER 1\n", encoding="utf-8")
    _claimed(monkeypatch, {"tool": "1.0"})

    [row], _ = managed_pages(cfg)

    assert row.drift
    assert row.note == "its man link is missing or was replaced"


def test_list_names_a_tool_maniac_has_no_page_for(tmp_path: Path) -> None:
    result = runner.invoke(app, ["list", "rg"])

    assert result.exit_code == 1
    assert "maniac installed no page for it; `maniac scan rg` shows its state." in (
        " ".join(result.output.split())
    )


def test_update_reinstalls_only_outdated_pages_and_keeps_a_pinned_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rule 1: only maniac's own pages, and of those only `outdated` ones;
    a pinned page is reinstalled for its recorded copy."""
    copy = _script(tmp_path / "proj" / "bin" / "pinned", "pinned 2")
    cfg = Config()
    _page(cfg, "fresh", version="1")
    _page(cfg, "stale", version="1")
    _page(cfg, "vague")
    _page(cfg, "pinned", version="pinned 1", binary=copy)
    _claimed(monkeypatch, {"fresh": "1", "stale": "2", "vague": "1"})
    calls: list[tuple[str, Path | None]] = []

    def run_install(tool: str, **kwargs: object) -> InstallOutcome:
        copy_arg = kwargs.get("copy")
        calls.append((tool, copy_arg if isinstance(copy_arg, Path) else None))
        assert kwargs["no_synthesize"] is True
        return InstallOutcome(
            tool=tool,
            tier=Tier.SYNTHESIS,
            detail="reinstalled",
            installed_path=tmp_path / f"{tool}.1",
        )

    monkeypatch.setattr("maniac.orchestration.install.run_install", run_install)

    result = runner.invoke(app, ["update", "--no-synthesize"])

    assert result.exit_code == 0, result.output
    assert calls == [("pinned", copy), ("stale", None)]


def test_update_named_says_why_a_page_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = Config()
    _page(cfg, "fresh", version="1")
    _page(cfg, "vague")
    _claimed(monkeypatch, {"fresh": "1", "vague": "1"})
    monkeypatch.setattr(
        "maniac.orchestration.install.run_install",
        lambda *a, **k: pytest.fail("nothing here is outdated"),
    )

    result = runner.invoke(app, ["update", "fresh", "vague", "rg"])

    output = " ".join(result.output.split())
    assert result.exit_code == 1
    assert "fresh up to date" in output
    assert "vague unknown, not updated; `maniac install vague` reinstalls it" in output
    assert "rg maniac installed no page for it." in output


@pytest.mark.skipif(os.name != "posix", reason="POSIX scripts")
def test_installing_a_pinned_copy_that_is_gone_is_refused(tmp_path: Path) -> None:
    from maniac.orchestration.install import InstallRefused, run_install

    gone = tmp_path / "proj" / "bin" / "tool"

    with pytest.raises(InstallRefused, match="which is gone"):
        run_install("tool", copy=gone, config=_config(tmp_path))
