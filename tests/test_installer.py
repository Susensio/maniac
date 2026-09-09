from pathlib import Path

import pytest

from maniac import manifest as manifest_module
from maniac.config import Config
from maniac.generation.compiler import build_provenance_header
from maniac.installer import (
    install_manpage,
    list_installed_manpages,
    read_provenance_header,
    uninstall_manpage,
)
from maniac.manifest import Tier


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


def test_install_manpage_clean(tmp_path: Path) -> None:
    src_file = tmp_path / "src" / "tool.1"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(".TH TOOL 1", encoding="utf-8")

    target_dir = tmp_path / "man1"
    installed = install_manpage(
        src_file, "tool", Tier.INSTALL_ROOT, str(src_file.parent), target_dir=target_dir
    )

    assert installed == target_dir / "tool.1"
    assert installed.exists()

    entry = manifest_module.lookup("tool")
    assert entry is not None
    assert entry.path == installed
    assert entry.tier is Tier.INSTALL_ROOT
    assert entry.source == str(src_file.parent)


def test_install_manpage_maniac_overwrite(tmp_path: Path) -> None:
    """A page the manifest already attributes to `tool` is ours to overwrite, no `--force`."""
    target_dir = tmp_path / "man1"
    target_dir.mkdir(parents=True)
    existing_dest = target_dir / "tool.1"
    existing_dest.write_text(".TH TOOL 1 old", encoding="utf-8")
    manifest_module.record(
        "tool",
        existing_dest,
        Tier.SYNTHESIS,
        "old-model",
        manifest_module.checksum_of(existing_dest),
    )

    src_file = tmp_path / "src" / "tool.1"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(".TH TOOL 1 new", encoding="utf-8")

    installed = install_manpage(
        src_file,
        "tool",
        Tier.SYNTHESIS,
        "new-model",
        target_dir=target_dir,
        force=False,
    )
    assert installed.exists()
    assert "new" in installed.read_text(encoding="utf-8")

    entry = manifest_module.lookup("tool")
    assert entry is not None
    assert entry.source == "new-model"


def test_install_manpage_foreign_without_force_fails(tmp_path: Path) -> None:
    target_dir = tmp_path / "man1"
    target_dir.mkdir(parents=True)
    existing_dest = target_dir / "tool.1"
    existing_dest.write_text(".TH TOOL 1 Official vendor doc", encoding="utf-8")

    src_file = tmp_path / "src" / "tool.1"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(".TH TOOL 1", encoding="utf-8")

    with pytest.raises(FileExistsError) as exc_info:
        install_manpage(
            src_file,
            "tool",
            Tier.SYNTHESIS,
            "model",
            target_dir=target_dir,
            force=False,
        )

    assert "foreign or vendor manpage already exists" in str(exc_info.value)
    assert existing_dest.read_text(encoding="utf-8") == ".TH TOOL 1 Official vendor doc"
    assert manifest_module.lookup("tool") is None


def test_install_manpage_foreign_with_force_creates_backup(tmp_path: Path) -> None:
    """The backup lands in `Config.backup_dir`, never as a sibling inside `man_dir`."""
    target_dir = tmp_path / "man1"
    target_dir.mkdir(parents=True)
    existing_dest = target_dir / "tool.1"
    existing_dest.write_text(".TH TOOL 1 Official vendor doc", encoding="utf-8")

    src_file = tmp_path / "src" / "tool.1"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(".TH TOOL 1 maniac", encoding="utf-8")

    installed = install_manpage(
        src_file, "tool", Tier.SYNTHESIS, "model", target_dir=target_dir, force=True
    )
    assert installed.exists()
    assert "maniac" in installed.read_text(encoding="utf-8")

    assert not (target_dir / "tool.1.maniac_bak").exists()

    entry = manifest_module.lookup("tool")
    assert entry is not None
    assert entry.path == installed
    assert entry.backup is not None
    assert entry.backup.parent == Config().backup_dir
    assert entry.backup.read_text(encoding="utf-8") == ".TH TOOL 1 Official vendor doc"


def test_install_manpage_reinstall_over_own_page_preserves_prior_backup(
    tmp_path: Path,
) -> None:
    """Reinstalling an owned page must not drop the vendor backup an earlier
    `--force` install took (P2 fix): it would become unrestorable on uninstall."""
    target_dir = tmp_path / "man1"
    target_dir.mkdir(parents=True)
    existing_dest = target_dir / "tool.1"
    existing_dest.write_text(".TH TOOL 1 Official vendor doc", encoding="utf-8")

    src_file = tmp_path / "src" / "tool.1"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(".TH TOOL 1 v1", encoding="utf-8")

    install_manpage(
        src_file, "tool", Tier.SYNTHESIS, "model-v1", target_dir=target_dir, force=True
    )
    first_backup = manifest_module.lookup("tool")
    assert first_backup is not None and first_backup.backup is not None

    src_file.write_text(".TH TOOL 1 v2", encoding="utf-8")
    install_manpage(
        src_file, "tool", Tier.SYNTHESIS, "model-v2", target_dir=target_dir, force=False
    )

    entry = manifest_module.lookup("tool")
    assert entry is not None
    assert entry.backup == first_backup.backup
    assert entry.backup.exists()


def test_install_manpage_reinstall_copy_failure_restores_prior_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed reinstall over an owned page must not orphan its vendor backup
    by forgetting the entry that is the only record of where it went (P2 fix)."""
    target_dir = tmp_path / "man1"
    target_dir.mkdir(parents=True)
    existing_dest = target_dir / "tool.1"
    existing_dest.write_text(".TH TOOL 1 Official vendor doc", encoding="utf-8")

    src_file = tmp_path / "src" / "tool.1"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(".TH TOOL 1 v1", encoding="utf-8")

    install_manpage(
        src_file, "tool", Tier.SYNTHESIS, "model-v1", target_dir=target_dir, force=True
    )
    first_entry = manifest_module.lookup("tool")
    assert first_entry is not None and first_entry.backup is not None

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("maniac.installer.shutil.copy2", _boom)

    src_file.write_text(".TH TOOL 1 v2", encoding="utf-8")
    with pytest.raises(OSError):
        install_manpage(
            src_file,
            "tool",
            Tier.SYNTHESIS,
            "model-v2",
            target_dir=target_dir,
            force=False,
        )

    entry = manifest_module.lookup("tool")
    assert entry == first_entry
    assert entry is not None and entry.backup is not None
    assert entry.backup.exists()


def test_install_manpage_reinstall_copy_failure_restores_prior_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The restore path carries the previous entry's version forward, not the
    new install's -- the copy never happened, so the old page (and the version
    it documents) is still what's on disk (ADR-0018)."""
    target_dir = tmp_path / "man1"
    target_dir.mkdir(parents=True)
    existing_dest = target_dir / "tool.1"
    existing_dest.write_text(".TH TOOL 1 Official vendor doc", encoding="utf-8")

    src_file = tmp_path / "src" / "tool.1"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(".TH TOOL 1 v1", encoding="utf-8")

    install_manpage(
        src_file,
        "tool",
        Tier.SYNTHESIS,
        "model-v1",
        target_dir=target_dir,
        force=True,
        version="1.0",
    )

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("maniac.installer.shutil.copy2", _boom)

    src_file.write_text(".TH TOOL 1 v2", encoding="utf-8")
    with pytest.raises(OSError):
        install_manpage(
            src_file,
            "tool",
            Tier.SYNTHESIS,
            "model-v2",
            target_dir=target_dir,
            force=False,
            version="2.0",
        )

    entry = manifest_module.lookup("tool")
    assert entry is not None
    assert entry.version == "1.0"


def test_install_manpage_copy_failure_forgets_manifest_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash between recording and copying leaves no orphaned manifest entry (ADR-0017)."""
    src_file = tmp_path / "src" / "tool.1"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(".TH TOOL 1", encoding="utf-8")
    target_dir = tmp_path / "man1"

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("maniac.installer.shutil.copy2", _boom)

    with pytest.raises(OSError):
        install_manpage(
            src_file, "tool", Tier.SYNTHESIS, "model", target_dir=target_dir
        )

    assert manifest_module.lookup("tool") is None


def test_uninstall_manpage_and_restore_backup(tmp_path: Path) -> None:
    """M3: a file restored from backup is not reported as removed -- it still exists.

    Restore uses the manifest's recorded `backup` path, not a reconstructed
    `<tool>.1.maniac_bak` -- the reconstruction was the pre-ADR-0017 bug that
    left a compressed page's backup unfindable.
    """
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True)

    installed_file = man_dir / "tool.1"
    installed_file.write_text(".TH TOOL 1 maniac", encoding="utf-8")

    backup_file = backup_dir / "tool.1"
    backup_file.write_text(".TH TOOL 1 Official vendor doc", encoding="utf-8")

    cfg = Config(man_dir=man_dir, output_dir=tmp_path / "data_manpages")
    manifest_module.record(
        "tool",
        installed_file,
        Tier.SYNTHESIS,
        "model",
        manifest_module.checksum_of(installed_file),
        backup=backup_file,
        config=cfg,
    )
    result = uninstall_manpage("tool", purge=False, config=cfg)

    assert installed_file not in result.removed
    assert installed_file.exists()  # Restored from backup!
    assert (
        installed_file.read_text(encoding="utf-8") == ".TH TOOL 1 Official vendor doc"
    )
    assert not backup_file.exists()
    assert manifest_module.lookup("tool", config=cfg) is None


def test_uninstall_manpage_compressed_page_restores_backup(tmp_path: Path) -> None:
    """The compressed-page case ADR-0017 closes: a `pandoc.1.gz` backup is found by path."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True)

    installed_file = man_dir / "pandoc.1.gz"
    installed_file.write_bytes(b"maniac-bytes")

    backup_file = backup_dir / "pandoc.1.gz"
    backup_file.write_bytes(b"vendor-bytes")

    cfg = Config(man_dir=man_dir, output_dir=tmp_path / "data_manpages")
    manifest_module.record(
        "pandoc",
        installed_file,
        Tier.INSTALL_ROOT,
        str(man_dir),
        manifest_module.checksum_of(installed_file),
        backup=backup_file,
        config=cfg,
    )
    result = uninstall_manpage("pandoc", purge=False, config=cfg)

    assert installed_file not in result.removed
    assert installed_file.exists()
    assert installed_file.read_bytes() == b"vendor-bytes"
    assert not backup_file.exists()


def test_uninstall_manpage_null_backup_removes_and_restores_nothing(
    tmp_path: Path,
) -> None:
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)

    installed_file = man_dir / "tool.1"
    installed_file.write_text(".TH TOOL 1", encoding="utf-8")

    cfg = Config(man_dir=man_dir, output_dir=tmp_path / "data_manpages")
    manifest_module.record(
        "tool",
        installed_file,
        Tier.SYNTHESIS,
        "model",
        manifest_module.checksum_of(installed_file),
        backup=None,
        config=cfg,
    )
    result = uninstall_manpage("tool", purge=False, config=cfg)

    assert installed_file in result.removed
    assert not installed_file.exists()


def test_uninstall_manpage_purge(tmp_path: Path) -> None:
    """M11: purge also removes the orphaned `{tool}_prompt.md` intermediate file."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    out_dir = tmp_path / "data_manpages"
    out_dir.mkdir(parents=True)
    inter_dir = tmp_path / "intermediate"
    inter_dir.mkdir(parents=True)

    installed_file = man_dir / "tool.1"
    installed_file.write_text(".TH TOOL 1 maniac", encoding="utf-8")

    md_file = out_dir / "tool.1.md"
    md_file.write_text("# TOOL", encoding="utf-8")

    ctx_file = inter_dir / "tool_context.md"
    ctx_file.write_text("Context", encoding="utf-8")

    prompt_file = inter_dir / "tool_prompt.md"
    prompt_file.write_text("Prompt", encoding="utf-8")

    cfg = Config(man_dir=man_dir, output_dir=out_dir, intermediate_dir=inter_dir)
    manifest_module.record(
        "tool",
        installed_file,
        Tier.SYNTHESIS,
        "model",
        manifest_module.checksum_of(installed_file),
        config=cfg,
    )
    result = uninstall_manpage("tool", purge=True, config=cfg)

    assert installed_file in result.removed
    assert not installed_file.exists()
    assert not md_file.exists()
    assert not ctx_file.exists()
    assert prompt_file in result.removed
    assert not prompt_file.exists()


def test_uninstall_manpage_foreign_kept(tmp_path: Path) -> None:
    """M2: a page absent from the manifest is left untouched and reported as such."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    out_dir = tmp_path / "data_manpages"
    out_dir.mkdir(parents=True)

    foreign_file = man_dir / "tool.1"
    foreign_file.write_text(".TH TOOL 1 Official vendor doc", encoding="utf-8")

    stored_roff = out_dir / "tool.1"
    stored_roff.write_text("roff", encoding="utf-8")

    cfg = Config(man_dir=man_dir, output_dir=out_dir)
    result = uninstall_manpage("tool", purge=False, config=cfg)

    assert result.foreign_kept == foreign_file
    assert result.modified_kept is None
    assert foreign_file.exists()  # untouched
    assert stored_roff in result.removed
    assert not stored_roff.exists()


def test_uninstall_manpage_checksum_mismatch_is_kept_modified(tmp_path: Path) -> None:
    """A recorded page whose bytes changed after install is ours, not foreign:
    reported via `modified_kept`, distinct from `foreign_kept`, which stays
    unset -- no new state, no restore, no forget."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True)

    installed_file = man_dir / "tool.1"
    installed_file.write_text(".TH TOOL 1 original", encoding="utf-8")
    recorded_checksum = manifest_module.checksum_of(installed_file)

    backup_file = backup_dir / "tool.1"
    backup_file.write_text(".TH TOOL 1 vendor", encoding="utf-8")

    cfg = Config(man_dir=man_dir, output_dir=tmp_path / "data_manpages")
    manifest_module.record(
        "tool",
        installed_file,
        Tier.SYNTHESIS,
        "model",
        recorded_checksum,
        backup=backup_file,
        config=cfg,
    )

    # Bytes changed after install -- no longer what MANIAC put there.
    installed_file.write_text(".TH TOOL 1 edited by something else", encoding="utf-8")

    result = uninstall_manpage("tool", purge=False, config=cfg)

    assert result.modified_kept == installed_file
    assert result.foreign_kept is None
    assert result.removed == []
    assert (
        installed_file.read_text(encoding="utf-8")
        == ".TH TOOL 1 edited by something else"
    )
    assert backup_file.exists()  # not restored
    assert manifest_module.lookup("tool", config=cfg) is not None  # not forgotten


def test_uninstall_manpage_force_overrides_checksum_mismatch(tmp_path: Path) -> None:
    """`--force` removes a modified page anyway, mirroring `install_manpage --force`."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)

    installed_file = man_dir / "tool.1"
    installed_file.write_text(".TH TOOL 1 original", encoding="utf-8")
    recorded_checksum = manifest_module.checksum_of(installed_file)

    cfg = Config(man_dir=man_dir, output_dir=tmp_path / "data_manpages")
    manifest_module.record(
        "tool", installed_file, Tier.SYNTHESIS, "model", recorded_checksum, config=cfg
    )

    installed_file.write_text(".TH TOOL 1 edited by something else", encoding="utf-8")

    result = uninstall_manpage("tool", purge=False, force=True, config=cfg)

    assert result.foreign_kept is None
    assert result.modified_kept is None
    assert installed_file in result.removed
    assert not installed_file.exists()
    assert manifest_module.lookup("tool", config=cfg) is None


def test_uninstall_manpage_vanished_entry_is_forgotten(tmp_path: Path) -> None:
    """A manifest entry whose file is already gone is cleaned up, not reported as removed."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    cfg = Config(man_dir=man_dir, output_dir=tmp_path / "data_manpages")
    manifest_module.record(
        "tool", man_dir / "tool.1", Tier.SYNTHESIS, "model", "deadbeef", config=cfg
    )

    result = uninstall_manpage("tool", purge=False, config=cfg)

    assert result.removed == []
    assert result.foreign_kept is None
    assert manifest_module.lookup("tool", config=cfg) is None


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


def test_round_trip_install_root(tmp_path: Path) -> None:
    src = tmp_path / "root" / "tool.1"
    src.parent.mkdir(parents=True)
    src.write_text(".TH TOOL 1", encoding="utf-8")

    installed = install_manpage(src, "tool", Tier.INSTALL_ROOT, str(src.parent))
    assert installed.exists()

    result = uninstall_manpage("tool")
    assert installed in result.removed
    assert not installed.exists()
    assert manifest_module.lookup("tool") is None


def test_round_trip_install_root_compressed(tmp_path: Path) -> None:
    """The case broken before ADR-0017: a compressed tier-1 page was unreachable to uninstall."""
    src = tmp_path / "root" / "pandoc.1.gz"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"\x1f\x8b\x08\x00not-really-gzip-but-bytes-are-enough")

    installed = install_manpage(src, "pandoc", Tier.INSTALL_ROOT, str(src.parent))
    assert installed.name == "pandoc.1.gz"
    assert installed.exists()

    result = uninstall_manpage("pandoc")
    assert installed in result.removed
    assert not installed.exists()
    assert manifest_module.lookup("pandoc") is None


def test_round_trip_repository(tmp_path: Path) -> None:
    src = tmp_path / "repo" / "tool.1"
    src.parent.mkdir(parents=True)
    src.write_text(".TH TOOL 1", encoding="utf-8")

    installed = install_manpage(src, "tool", Tier.REPOSITORY, "owner/repo")
    assert installed.exists()

    result = uninstall_manpage("tool")
    assert installed in result.removed
    assert not installed.exists()
    assert manifest_module.lookup("tool") is None


def test_round_trip_synthesis(tmp_path: Path) -> None:
    src = tmp_path / "out" / "tool.1"
    src.parent.mkdir(parents=True)
    header = build_provenance_header("tool", model="test-model")
    src.write_text(header + ".TH TOOL 1", encoding="utf-8")

    installed = install_manpage(src, "tool", Tier.SYNTHESIS, "test-model")
    assert installed.exists()

    result = uninstall_manpage("tool")
    assert installed in result.removed
    assert not installed.exists()
    assert manifest_module.lookup("tool") is None
