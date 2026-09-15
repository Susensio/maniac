"""Filesystem transitions at the reconciliation seam (ADR-0017, ADR-0028, ADR-0032)."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from maniac import installer, lifecycle, manifest
from maniac.config import Config
from maniac.generation.compiler import build_provenance_header
from maniac.lifecycle import list_installed_manpages, read_provenance_header
from maniac.manifest import Entry, Tier

from .manifest_support import reconcile, record_entry


def _config(tmp_path: Path) -> Config:
    return Config(manifest_path=tmp_path / "state" / "installed.json")


@pytest.fixture(autouse=True)
def _isolated_xdg_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep default Config instances away from a developer's MANIAC state."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


def test_empty_manifest_is_not_reseeded_from_headers(tmp_path: Path) -> None:
    """A manifest file that exists (even with zero entries) is never mistaken for absent."""
    cfg = _config(tmp_path)
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    header = build_provenance_header("tool", model="Flash")
    (man_dir / "tool.1").write_text(header + ".TH TOOL 1", encoding="utf-8")
    full_cfg = Config(manifest_path=cfg.manifest_path, man_dir=man_dir)

    cfg.manifest_path.parent.mkdir(parents=True)
    cfg.manifest_path.write_text(
        json.dumps({"version": 1, "entries": {}}), encoding="utf-8"
    )

    assert reconcile(config=full_cfg) == {}


def test_migration_ignores_a_header_carrying_page_only_in_output_dir(
    tmp_path: Path,
) -> None:
    """`output_dir` is a staging copy, not proof of an installed page (P2 fix)."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True)
    header = build_provenance_header("tool", model="Flash")
    (output_dir / "tool.1").write_text(header + ".TH TOOL 1", encoding="utf-8")

    cfg = Config(
        manifest_path=tmp_path / "state" / "installed.json",
        man_dir=man_dir,
        output_dir=output_dir,
    )
    assert reconcile(config=cfg) == {}


def test_migration_seeds_and_links_a_header_carrying_page(tmp_path: Path) -> None:
    """First load migrates a header-seeded page to its durable target."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    header = build_provenance_header("tool", model="Gemini 3.7 Flash")
    (man_dir / "tool.1").write_text(header + ".TH TOOL 1", encoding="utf-8")

    cfg = Config(manifest_path=tmp_path / "state" / "installed.json", man_dir=man_dir)
    entries = reconcile(config=cfg)

    assert set(entries) == {"tool"}
    assert entries["tool"].tier is Tier.SYNTHESIS
    assert entries["tool"].source == "Gemini 3.7 Flash"
    assert entries["tool"].path == man_dir / "tool.1"
    assert entries["tool"].checksum == manifest.checksum_of(man_dir / "tool.1")
    assert entries["tool"].target == cfg.output_dir / "tool.1"
    assert (man_dir / "tool.1").is_symlink()
    assert (man_dir / "tool.1").resolve() == entries["tool"].target
    # Migration persists so it never reruns.
    assert cfg.manifest_path.exists()


def test_migration_does_not_recover_headerless_tier1_pages(tmp_path: Path) -> None:
    """Tier-1/2 pages carry nothing recoverable -- accepted cost of the migration (ADR-0017)."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    (man_dir / "vendor.1").write_text(".TH VENDOR 1 no header", encoding="utf-8")

    cfg = Config(manifest_path=tmp_path / "state" / "installed.json", man_dir=man_dir)
    assert reconcile(config=cfg) == {}


def test_migration_converts_legacy_copy_to_durable_link(tmp_path: Path) -> None:
    man_dir = tmp_path / "man1"
    man_dir.mkdir()
    page = man_dir / "tool.1"
    page.write_bytes(b"legacy bytes")
    backup = tmp_path / "backups" / "tool.1"
    backup.parent.mkdir()
    backup.write_bytes(b"vendor bytes")
    cfg = Config(
        manifest_path=tmp_path / "state" / "installed.json",
        man_dir=man_dir,
        output_dir=tmp_path / "data",
        backup_dir=backup.parent,
    )
    record_entry(
        "tool",
        page,
        Tier.REPOSITORY,
        "owner/tool",
        manifest.checksum_of(page),
        backup=backup,
        version="1.2.3",
        source_uri="https://example.test/tool.1",
        config=cfg,
    )

    entry = reconcile(config=cfg)["tool"]

    assert page.is_symlink()
    assert entry.target == cfg.output_dir / page.name
    assert page.resolve() == entry.target
    assert entry.target.read_bytes() == b"legacy bytes"
    assert entry.backup == backup
    assert entry.version == "1.2.3"
    assert entry.source_uri == "https://example.test/tool.1"


def test_migration_retains_changed_legacy_copy(tmp_path: Path) -> None:
    man_dir = tmp_path / "man1"
    man_dir.mkdir()
    page = man_dir / "tool.1"
    page.write_text("original", encoding="utf-8")
    cfg = Config(
        manifest_path=tmp_path / "state" / "installed.json",
        man_dir=man_dir,
        output_dir=tmp_path / "data",
    )
    record_entry(
        "tool", page, Tier.SYNTHESIS, "model", manifest.checksum_of(page), config=cfg
    )
    page.write_text("user changed", encoding="utf-8")

    entry = reconcile(config=cfg)["tool"]

    assert not page.is_symlink()
    assert page.read_text(encoding="utf-8") == "user changed"
    assert entry.target is None
    assert not cfg.output_dir.exists()


def test_migration_relinks_an_unmodified_vendor_copy_to_its_provider_page(
    tmp_path: Path,
) -> None:
    root = tmp_path / "provider" / "tool" / "1.0.0"
    provider_page = root / "share" / "man" / "man1" / "tool.1"
    provider_page.parent.mkdir(parents=True)
    provider_page.write_text(".TH TOOL 1\n", encoding="utf-8")
    man_dir = tmp_path / "man1"
    man_dir.mkdir()
    installed = man_dir / "tool.1"
    durable_target = tmp_path / "data" / "tool.1"
    durable_target.parent.mkdir()
    durable_target.write_text(
        provider_page.read_text(encoding="utf-8"), encoding="utf-8"
    )
    installed.symlink_to(durable_target)
    cfg = Config(
        manifest_path=tmp_path / "state" / "installed.json",
        man_dir=man_dir,
        output_dir=durable_target.parent,
    )
    record_entry(
        "tool",
        installed,
        Tier.INSTALL_ROOT,
        str(root),
        manifest.checksum_of(durable_target),
        target=durable_target,
        config=cfg,
    )

    entry = reconcile(config=cfg)["tool"]

    assert installed.readlink() == provider_page.absolute()
    assert entry.target == provider_page.absolute()
    assert entry.provider_target is True
    assert entry.checksum == manifest.checksum_of(provider_page)
    assert not durable_target.exists()


def test_migration_retains_a_modified_vendor_copy(tmp_path: Path) -> None:
    root = tmp_path / "provider" / "tool" / "1.0.0"
    provider_page = root / "share" / "man" / "man1" / "tool.1"
    provider_page.parent.mkdir(parents=True)
    provider_page.write_text(".TH TOOL 1 provider\n", encoding="utf-8")
    man_dir = tmp_path / "man1"
    man_dir.mkdir()
    installed = man_dir / "tool.1"
    durable_target = tmp_path / "data" / "tool.1"
    durable_target.parent.mkdir()
    durable_target.write_text(".TH TOOL 1 user changed\n", encoding="utf-8")
    installed.symlink_to(durable_target)
    cfg = Config(
        manifest_path=tmp_path / "state" / "installed.json",
        man_dir=man_dir,
        output_dir=durable_target.parent,
    )
    record_entry(
        "tool",
        installed,
        Tier.INSTALL_ROOT,
        str(root),
        manifest.checksum_of(provider_page),
        target=durable_target,
        config=cfg,
    )

    entry = reconcile(config=cfg)["tool"]

    assert installed.readlink() == durable_target
    assert entry.target == durable_target
    assert entry.provider_target is False
    assert durable_target.exists()


def test_migration_relocates_a_stray_backup_out_of_man_dir(tmp_path: Path) -> None:
    """A `.maniac_bak` sibling of a header-carrying page is moved into `backup_dir`
    and attributed to that page's manifest entry, instead of left in `man_dir`."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    header = build_provenance_header("tool", model="Flash")
    (man_dir / "tool.1").write_text(header + ".TH TOOL 1", encoding="utf-8")
    stray_backup = man_dir / "tool.1.maniac_bak"
    stray_backup.write_text(".TH TOOL 1 vendor", encoding="utf-8")

    cfg = Config(
        manifest_path=tmp_path / "state" / "installed.json",
        man_dir=man_dir,
        backup_dir=tmp_path / "state" / "backups",
    )
    entries = reconcile(config=cfg)

    assert not stray_backup.exists()
    relocated = cfg.backup_dir / "tool.1"
    assert relocated.exists()
    assert relocated.read_text(encoding="utf-8") == ".TH TOOL 1 vendor"
    assert entries["tool"].backup == relocated


def test_migration_leaves_an_unattributable_backup_in_place(tmp_path: Path) -> None:
    """A `.maniac_bak` matching no manifest entry is left where it is."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    stray_backup = man_dir / "nonexistent.1.maniac_bak"
    stray_backup.write_bytes(b"vendor-bytes")

    cfg = Config(
        manifest_path=tmp_path / "state" / "installed.json",
        man_dir=man_dir,
        backup_dir=tmp_path / "state" / "backups",
    )
    reconcile(config=cfg)

    assert stray_backup.exists()


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


def test_read_provenance_header(tmp_path: Path) -> None:
    f = tmp_path / "mytool.1"
    header = build_provenance_header(tool_name="mytool", model="Gemini 3.7 Flash")
    f.write_text(header + ".TH MYTOOL 1\n", encoding="utf-8")

    meta = read_provenance_header(f)
    assert meta is not None
    assert meta["tool"] == "mytool"
    assert meta["model"] == "Gemini 3.7 Flash"
    assert "date" in meta

    foreign_file = tmp_path / "vendor.1"
    foreign_file.write_text(".TH VENDOR 1\nOfficial manual", encoding="utf-8")
    assert read_provenance_header(foreign_file) is None


def test_read_provenance_header_permission_denied(tmp_path: Path) -> None:
    """Low: EACCES must not be silently treated as 'foreign page'."""
    f = tmp_path / "tool.1"
    f.write_text(".TH TOOL 1", encoding="utf-8")
    f.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            read_provenance_header(f)
    finally:
        f.chmod(0o644)


def test_list_installed_manpages(tmp_path: Path) -> None:
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)

    header1 = build_provenance_header("tool1", model="Flash")
    (man_dir / "tool1.1").write_text(header1 + ".TH TOOL1 1", encoding="utf-8")

    (man_dir / "vendor.1").write_text(".TH VENDOR 1 vendor page", encoding="utf-8")

    cfg = Config(man_dir=man_dir, output_dir=tmp_path / "data_empty")
    items = list_installed_manpages(config=cfg)

    assert len(items) == 1
    assert items[0]["tool"] == "tool1"
    assert items[0]["model"] == "Flash"


def _durable_vendor_copy_fixture(tmp_path: Path) -> tuple[Config, Path, Path, Path]:
    """Config, manpath entry, superseded durable copy and provider page of a recorded
    INSTALL_ROOT entry whose vendor copy ADR-0032's migration can relink."""
    root = tmp_path / "provider" / "tool" / "1.0.0"
    provider_page = root / "share" / "man" / "man1" / "tool.1"
    provider_page.parent.mkdir(parents=True)
    provider_page.write_text(".TH TOOL 1\n", encoding="utf-8")
    man_dir = tmp_path / "man1"
    man_dir.mkdir()
    installed = man_dir / "tool.1"
    durable_target = tmp_path / "data" / "tool.1"
    durable_target.parent.mkdir()
    durable_target.write_text(
        provider_page.read_text(encoding="utf-8"), encoding="utf-8"
    )
    installed.symlink_to(durable_target)
    cfg = Config(
        manifest_path=tmp_path / "state" / "installed.json",
        man_dir=man_dir,
        output_dir=durable_target.parent,
    )
    record_entry(
        "tool",
        installed,
        Tier.INSTALL_ROOT,
        str(root),
        manifest.checksum_of(durable_target),
        target=durable_target,
        config=cfg,
    )
    return cfg, installed, durable_target, provider_page


def test_uninstall_reports_the_durable_copy_migration_deleted(tmp_path: Path) -> None:
    """Uninstall's own reconcile relinks to the provider page and unlinks the
    superseded copy before ownership is judged; that removal must be reported."""
    cfg, _installed, durable_target, _provider_page = _durable_vendor_copy_fixture(
        tmp_path
    )

    result = installer.uninstall_manpage("tool", config=cfg)

    assert not durable_target.exists()
    assert durable_target in result.removed


def test_read_only_reconcile_reports_no_removals(tmp_path: Path) -> None:
    """A caller with no removal report to write into passes no sink; the same
    migration runs and nothing is reported anywhere."""
    cfg, installed, durable_target, provider_page = _durable_vendor_copy_fixture(
        tmp_path
    )

    entry = reconcile(config=cfg)["tool"]

    assert installed.readlink() == provider_page.absolute()
    assert entry.provider_target is True
    assert not durable_target.exists()


def test_reconcile_reports_no_removal_for_a_retained_shared_target(
    tmp_path: Path,
) -> None:
    """A durable copy still serving a second entry is kept, so nothing is reported."""
    cfg, _installed, durable_target, _provider_page = _durable_vendor_copy_fixture(
        tmp_path
    )
    entries = manifest.load(cfg)
    entries["other"] = replace(entries["tool"], path=cfg.man_dir / "other.1")
    manifest.save(entries, cfg)

    removed: list[Path] = []
    reconcile(config=cfg, removed=removed)

    assert durable_target.exists()
    assert removed == []


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
