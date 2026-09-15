"""Filesystem transitions at the durable-target seam (ADR-0017, ADR-0031)."""

from pathlib import Path

import pytest

from maniac import lifecycle
from maniac.config import Config
from maniac.manifest import Entry, Tier


@pytest.fixture(autouse=True)
def _isolated_xdg_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep default Config instances away from a developer's MANIAC state."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


def _entry(*, path: Path, target: Path | None, provider_target: bool = False) -> Entry:
    return Entry(
        path=path,
        tier=Tier.REPOSITORY,
        source="owner/tool",
        checksum="deadbeef",
        target=target,
        provider_target=provider_target,
    )


def test_discard_durable_target_removes_a_maniac_owned_target(tmp_path: Path) -> None:
    cfg = Config(
        manifest_path=tmp_path / "state" / "installed.json",
        output_dir=tmp_path / "durable",
    )
    target = cfg.output_dir / "tool.1"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"page bytes")
    entry = _entry(path=tmp_path / "man1" / "tool.1", target=target)

    assert lifecycle.discard_durable_target(entry, cfg, {}) == target
    assert not target.exists()


def test_discard_durable_target_keeps_a_provider_owned_target(tmp_path: Path) -> None:
    """ADR-0031: a provider-owned target survives even when it sits under `output_dir`."""
    cfg = Config(
        manifest_path=tmp_path / "state" / "installed.json",
        output_dir=tmp_path / "durable",
    )
    target = cfg.output_dir / "tool.1"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"page bytes")
    entry = _entry(
        path=tmp_path / "man1" / "tool.1", target=target, provider_target=True
    )

    assert lifecycle.discard_durable_target(entry, cfg, {}) is None
    assert target.exists()


def test_discard_durable_target_keeps_a_target_outside_output_dir(
    tmp_path: Path,
) -> None:
    cfg = Config(
        manifest_path=tmp_path / "state" / "installed.json",
        output_dir=tmp_path / "durable",
    )
    target = tmp_path / "elsewhere" / "tool.1"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"page bytes")
    entry = _entry(path=tmp_path / "man1" / "tool.1", target=target)

    assert lifecycle.discard_durable_target(entry, cfg, {}) is None
    assert target.exists()


def test_discard_durable_target_tolerates_an_already_missing_target(
    tmp_path: Path,
) -> None:
    cfg = Config(
        manifest_path=tmp_path / "state" / "installed.json",
        output_dir=tmp_path / "durable",
    )
    target = cfg.output_dir / "tool.1"
    entry = _entry(path=tmp_path / "man1" / "tool.1", target=target)

    assert lifecycle.discard_durable_target(entry, cfg, {}) is None


def test_discard_durable_target_returns_none_without_a_recorded_target(
    tmp_path: Path,
) -> None:
    cfg = Config(manifest_path=tmp_path / "state" / "installed.json")
    entry = _entry(path=tmp_path / "man1" / "tool.1", target=None)

    assert lifecycle.discard_durable_target(entry, cfg, {}) is None


def test_discard_durable_target_keeps_a_target_another_entry_records(
    tmp_path: Path,
) -> None:
    """Two entries can share one durable target; the first uninstall must not take it.

    Unlinking it would dangle the survivor's link and leave its checksum
    unverifiable, which is what makes an entry un-uninstallable.
    """
    cfg = Config(
        manifest_path=tmp_path / "state" / "installed.json",
        output_dir=tmp_path / "durable",
    )
    target = cfg.output_dir / "page.1"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"page bytes")
    going = _entry(path=tmp_path / "man1" / "page.1", target=target)
    staying = _entry(path=tmp_path / "man2" / "page.1", target=target)

    assert lifecycle.discard_durable_target(going, cfg, {"other": staying}) is None
    assert target.exists()


def test_discard_durable_target_removes_a_target_its_last_user_releases(
    tmp_path: Path,
) -> None:
    """Once no remaining entry records it, the shared target goes with the last one."""
    cfg = Config(
        manifest_path=tmp_path / "state" / "installed.json",
        output_dir=tmp_path / "durable",
    )
    target = cfg.output_dir / "page.1"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"page bytes")
    unrelated = _entry(
        path=tmp_path / "man1" / "tool.1", target=cfg.output_dir / "tool.1"
    )
    last = _entry(path=tmp_path / "man2" / "page.1", target=target)

    assert lifecycle.discard_durable_target(last, cfg, {"tool": unrelated}) == target
    assert not target.exists()
