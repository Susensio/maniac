from pathlib import Path

import pytest

from maniac import manifest as manifest_module
from maniac.config import Config
from maniac.generation.compiler import build_provenance_header
from maniac.installer import install_manpage, uninstall_manpage
from maniac.manifest import Tier

from .manifest_support import record_entry


@pytest.fixture(autouse=True)
def _isolated_xdg_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep default Config instances away from a developer's MANIAC state."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


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


def test_install_materializes_source_before_linking(tmp_path: Path) -> None:
    source = tmp_path / "cache" / "tool.1"
    source.parent.mkdir()
    source.write_text(".TH TOOL 1 repository", encoding="utf-8")
    cfg = Config(
        man_dir=tmp_path / "man1",
        output_dir=tmp_path / "durable",
        manifest_path=tmp_path / "state" / "installed.json",
    )

    installed = install_manpage(
        source, "tool", Tier.REPOSITORY, "owner/tool", config=cfg
    )

    entry = manifest_module.lookup("tool", config=cfg)
    assert entry is not None and entry.target == cfg.output_dir / "tool.1"
    assert installed.is_symlink()
    assert installed.resolve() == entry.target
    assert entry.target.read_text(encoding="utf-8") == source.read_text(
        encoding="utf-8"
    )
    assert not entry.target.is_relative_to(source.parent)


def test_install_root_direct_link_requires_explicit_durable_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "provider" / "tool.1"
    source.parent.mkdir()
    source.write_text(".TH TOOL 1 vendor", encoding="utf-8")
    cfg = Config(
        man_dir=tmp_path / "man1",
        output_dir=tmp_path / "durable",
        manifest_path=tmp_path / "state" / "installed.json",
    )

    installed = install_manpage(
        source,
        "tool",
        Tier.INSTALL_ROOT,
        str(source.parent),
        durable_source=True,
        config=cfg,
    )

    entry = manifest_module.lookup("tool", config=cfg)
    assert entry is not None and entry.target == source.absolute()
    assert installed.resolve() == source
    assert not cfg.output_dir.exists()


def test_uninstall_removes_a_concrete_provider_target_but_keeps_its_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "mise" / "1.0.0" / "share" / "man" / "man1" / "tool.1"
    source.parent.mkdir(parents=True)
    source.write_text(".TH TOOL 1 old", encoding="utf-8")
    cfg = Config(
        man_dir=tmp_path / "man1",
        output_dir=tmp_path / "mise",
        manifest_path=tmp_path / "state" / "installed.json",
    )

    installed = install_manpage(
        source,
        "tool",
        Tier.INSTALL_ROOT,
        "mise-root",
        durable_source=True,
        provider_target=True,
        config=cfg,
    )
    source.write_text(".TH TOOL 1 updated", encoding="utf-8")

    result = uninstall_manpage("tool", config=cfg)

    assert result.modified_kept == []
    assert installed in result.removed
    assert not installed.exists()
    assert source.exists()


def test_install_manpage_maniac_overwrite(tmp_path: Path) -> None:
    """A page the manifest already attributes to `tool` is ours to overwrite, no `--force`."""
    target_dir = tmp_path / "man1"
    old_src = tmp_path / "old-src" / "tool.1"
    old_src.parent.mkdir(parents=True)
    old_src.write_text(".TH TOOL 1 old", encoding="utf-8")
    install_manpage(
        old_src,
        "tool",
        Tier.SYNTHESIS,
        "old-model",
        target_dir=target_dir,
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


def test_install_does_not_authorize_a_different_manpath_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / "tool.1"
    source.parent.mkdir()
    source.write_text(".TH TOOL 1 managed", encoding="utf-8")
    cfg = Config(
        output_dir=tmp_path / "durable",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    install_manpage(
        source,
        "tool",
        Tier.SYNTHESIS,
        "model",
        target_dir=tmp_path / "old-man1",
        config=cfg,
    )
    new_dir = tmp_path / "new-man1"
    new_dir.mkdir()
    foreign = new_dir / "tool.1"
    foreign.write_text(".TH TOOL 1 foreign", encoding="utf-8")

    with pytest.raises(FileExistsError):
        install_manpage(
            source,
            "tool",
            Tier.SYNTHESIS,
            "model",
            target_dir=new_dir,
            config=cfg,
        )

    assert foreign.read_text(encoding="utf-8") == ".TH TOOL 1 foreign"


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
    output_dir = tmp_path / "data_manpages"
    output_dir.mkdir(parents=True)

    target = output_dir / "tool.1"
    target.write_text(".TH TOOL 1 maniac", encoding="utf-8")
    installed_file = man_dir / "tool.1"
    installed_file.symlink_to(target)

    backup_file = backup_dir / "tool.1"
    backup_file.write_text(".TH TOOL 1 Official vendor doc", encoding="utf-8")

    cfg = Config(man_dir=man_dir, output_dir=output_dir)
    record_entry(
        "tool",
        installed_file,
        Tier.SYNTHESIS,
        "model",
        manifest_module.checksum_of(target),
        backup=backup_file,
        target=target,
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
    output_dir = tmp_path / "data_manpages"
    output_dir.mkdir(parents=True)

    target = output_dir / "pandoc.1.gz"
    target.write_bytes(b"maniac-bytes")
    installed_file = man_dir / "pandoc.1.gz"
    installed_file.symlink_to(target)

    backup_file = backup_dir / "pandoc.1.gz"
    backup_file.write_bytes(b"vendor-bytes")

    cfg = Config(man_dir=man_dir, output_dir=output_dir)
    record_entry(
        "pandoc",
        installed_file,
        Tier.INSTALL_ROOT,
        str(man_dir),
        manifest_module.checksum_of(target),
        backup=backup_file,
        target=target,
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
    output_dir = tmp_path / "data_manpages"
    output_dir.mkdir(parents=True)

    target = output_dir / "tool.1"
    target.write_text(".TH TOOL 1", encoding="utf-8")
    installed_file = man_dir / "tool.1"
    installed_file.symlink_to(target)

    cfg = Config(man_dir=man_dir, output_dir=output_dir)
    record_entry(
        "tool",
        installed_file,
        Tier.SYNTHESIS,
        "model",
        manifest_module.checksum_of(target),
        backup=None,
        target=target,
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

    target = out_dir / "tool.1"
    target.write_text(".TH TOOL 1 maniac", encoding="utf-8")
    installed_file = man_dir / "tool.1"
    installed_file.symlink_to(target)

    md_file = out_dir / "tool.1.md"
    md_file.write_text("# TOOL", encoding="utf-8")

    ctx_file = inter_dir / "tool_context.md"
    ctx_file.write_text("Context", encoding="utf-8")

    prompt_file = inter_dir / "tool_prompt.md"
    prompt_file.write_text("Prompt", encoding="utf-8")

    cfg = Config(man_dir=man_dir, output_dir=out_dir, intermediate_dir=inter_dir)
    record_entry(
        "tool",
        installed_file,
        Tier.SYNTHESIS,
        "model",
        manifest_module.checksum_of(target),
        target=target,
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
    assert result.modified_kept == []
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
    record_entry(
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

    assert result.modified_kept == [installed_file]
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
    cfg = Config(man_dir=man_dir, output_dir=tmp_path / "data_manpages")
    source = tmp_path / "source" / "tool.1"
    source.parent.mkdir()
    source.write_text(".TH TOOL 1 original", encoding="utf-8")
    install_manpage(source, "tool", Tier.SYNTHESIS, "model", config=cfg)
    entry = manifest_module.lookup("tool", config=cfg)
    assert entry is not None and entry.target is not None
    entry.target.write_text(".TH TOOL 1 edited by something else", encoding="utf-8")

    result = uninstall_manpage("tool", purge=False, force=True, config=cfg)

    assert result.foreign_kept is None
    assert result.modified_kept == []
    assert installed_file in result.removed
    assert not installed_file.exists()
    assert manifest_module.lookup("tool", config=cfg) is None


def test_uninstall_preserves_retargeted_link_and_backup(tmp_path: Path) -> None:
    source = tmp_path / "source" / "tool.1"
    source.parent.mkdir()
    source.write_text(".TH TOOL 1 maniac", encoding="utf-8")
    backup = tmp_path / "backups" / "tool.1"
    backup.parent.mkdir()
    backup.write_text(".TH TOOL 1 vendor", encoding="utf-8")
    cfg = Config(
        man_dir=tmp_path / "man1",
        output_dir=tmp_path / "durable",
        backup_dir=backup.parent,
        manifest_path=tmp_path / "state" / "installed.json",
    )
    installed = install_manpage(source, "tool", Tier.SYNTHESIS, "model", config=cfg)
    entry = manifest_module.lookup("tool", config=cfg)
    assert entry is not None and entry.target is not None
    record_entry(
        "tool",
        installed,
        entry.tier,
        entry.source,
        entry.checksum,
        backup=backup,
        target=entry.target,
        config=cfg,
    )
    replacement = tmp_path / "replacement.1"
    replacement.write_text(".TH TOOL 1 user", encoding="utf-8")
    installed.unlink()
    installed.symlink_to(replacement)

    result = uninstall_manpage("tool", config=cfg)

    assert result.modified_kept == [installed]
    assert installed.resolve() == replacement
    assert backup.exists()
    assert manifest_module.lookup("tool", config=cfg) is not None


def test_uninstall_preserves_link_retargeted_through_an_alias(tmp_path: Path) -> None:
    source = tmp_path / "source" / "tool.1"
    source.parent.mkdir()
    source.write_text(".TH TOOL 1 maniac", encoding="utf-8")
    cfg = Config(
        man_dir=tmp_path / "man1",
        output_dir=tmp_path / "durable",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    installed = install_manpage(source, "tool", Tier.SYNTHESIS, "model", config=cfg)
    entry = manifest_module.lookup("tool", config=cfg)
    assert entry is not None and entry.target is not None
    alias = tmp_path / "alias.1"
    alias.symlink_to(entry.target)
    installed.unlink()
    installed.symlink_to(alias)

    result = uninstall_manpage("tool", config=cfg)

    assert result.modified_kept == [installed]
    assert installed.readlink() == alias
    assert manifest_module.lookup("tool", config=cfg) is not None


def test_uninstall_accepts_a_recorded_relative_link_target(tmp_path: Path) -> None:
    man_dir = tmp_path / "man1"
    man_dir.mkdir()
    target = tmp_path / "durable" / "tool.1"
    target.parent.mkdir()
    target.write_text(".TH TOOL 1 maniac", encoding="utf-8")
    installed = man_dir / "tool.1"
    relative_target = Path("../durable/tool.1")
    installed.symlink_to(relative_target)
    cfg = Config(
        man_dir=man_dir,
        output_dir=target.parent,
        manifest_path=tmp_path / "state" / "installed.json",
    )
    record_entry(
        "tool",
        installed,
        Tier.SYNTHESIS,
        "model",
        manifest_module.checksum_of(target),
        target=relative_target,
        config=cfg,
    )

    result = uninstall_manpage("tool", config=cfg)

    assert installed in result.removed
    assert not installed.exists()


def test_uninstall_preserves_replaced_link(tmp_path: Path) -> None:
    source = tmp_path / "source" / "tool.1"
    source.parent.mkdir()
    source.write_text(".TH TOOL 1 maniac", encoding="utf-8")
    cfg = Config(
        man_dir=tmp_path / "man1",
        output_dir=tmp_path / "durable",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    installed = install_manpage(source, "tool", Tier.SYNTHESIS, "model", config=cfg)
    installed.unlink()
    installed.write_text(".TH TOOL 1 user replacement", encoding="utf-8")

    result = uninstall_manpage("tool", config=cfg)

    assert result.modified_kept == [installed]
    assert installed.read_text(encoding="utf-8") == ".TH TOOL 1 user replacement"
    assert manifest_module.lookup("tool", config=cfg) is not None


def test_uninstall_preserves_dangling_owned_link(tmp_path: Path) -> None:
    source = tmp_path / "source" / "tool.1"
    source.parent.mkdir()
    source.write_text(".TH TOOL 1 maniac", encoding="utf-8")
    cfg = Config(
        man_dir=tmp_path / "man1",
        output_dir=tmp_path / "durable",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    installed = install_manpage(source, "tool", Tier.SYNTHESIS, "model", config=cfg)
    entry = manifest_module.lookup("tool", config=cfg)
    assert entry is not None and entry.target is not None
    entry.target.unlink()

    result = uninstall_manpage("tool", config=cfg)

    assert result.modified_kept == [installed]
    assert installed.is_symlink()
    assert not installed.exists()
    assert manifest_module.lookup("tool", config=cfg) is not None


def test_uninstall_manpage_vanished_entry_is_forgotten(tmp_path: Path) -> None:
    """A manifest entry whose file is already gone is cleaned up, not reported as removed."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    cfg = Config(man_dir=man_dir, output_dir=tmp_path / "data_manpages")
    record_entry(
        "tool", man_dir / "tool.1", Tier.SYNTHESIS, "model", "deadbeef", config=cfg
    )

    result = uninstall_manpage("tool", purge=False, config=cfg)

    assert result.removed == []
    assert result.foreign_kept is None
    assert manifest_module.lookup("tool", config=cfg) is None


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
    durable_target = Config().output_dir / "tool.1"
    assert durable_target.exists()

    result = uninstall_manpage("tool")
    assert installed in result.removed
    assert not installed.exists()
    assert durable_target in result.removed
    assert not durable_target.exists()
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


_EZA_RELEASE = ("eza.1", "eza_colors.5", "eza_colors-explanation.5")


def _eza_config(tmp_path: Path) -> Config:
    return Config(
        man_dir=tmp_path / "man" / "man1",
        output_dir=tmp_path / "durable",
        backup_dir=tmp_path / "backups",
        intermediate_dir=tmp_path / "intermediate",
        manifest_path=tmp_path / "state" / "installed.json",
    )


def _section_dir(cfg: Config, name: str) -> Path:
    section = Path(name).suffix.removeprefix(".")
    return cfg.man_dir if section == "1" else cfg.man_dir.parent / f"man{section}"


def _install_eza_release(
    tmp_path: Path, cfg: Config, *, vendor_pages: bool = False
) -> dict[str, Path]:
    """Install eza's three-page release archive as one group, keyed by manpage owner."""
    source_dir = tmp_path / "release"
    source_dir.mkdir()
    installed: dict[str, Path] = {}
    for name in _EZA_RELEASE:
        source = source_dir / name
        source.write_text(f".TH {name} maniac\n", encoding="utf-8")
        dest_dir = _section_dir(cfg, name)
        dest_dir.mkdir(parents=True, exist_ok=True)
        if vendor_pages:
            (dest_dir / name).write_text(f"vendor {name}\n", encoding="utf-8")
        owner = Path(name).with_suffix("").name
        installed[owner] = install_manpage(
            source,
            owner,
            Tier.REPOSITORY,
            "eza-community/eza",
            target_dir=dest_dir,
            force=vendor_pages,
            group="eza",
            config=cfg,
        )
    return installed


def test_install_records_every_release_page_under_one_group(tmp_path: Path) -> None:
    """The three pages of one eza release share a group naming the primary."""
    cfg = _eza_config(tmp_path)
    _install_eza_release(tmp_path, cfg)

    entries = manifest_module.load(cfg)
    assert set(entries) == {"eza", "eza_colors", "eza_colors-explanation"}
    assert {entry.group for entry in entries.values()} == {"eza"}


def test_uninstalling_the_primary_removes_every_group_member(tmp_path: Path) -> None:
    cfg = _eza_config(tmp_path)
    installed = _install_eza_release(tmp_path, cfg)

    result = uninstall_manpage("eza", config=cfg)

    for path in installed.values():
        assert path in result.removed
        assert not path.exists()
    assert manifest_module.load(cfg) == {}


def test_uninstalling_a_companion_removes_every_group_member(tmp_path: Path) -> None:
    """A companion reaches the whole unit, primary included -- the ambiguity
    `source_uri` could not resolve."""
    cfg = _eza_config(tmp_path)
    installed = _install_eza_release(tmp_path, cfg)

    result = uninstall_manpage("eza_colors", config=cfg)

    for path in installed.values():
        assert path in result.removed
        assert not path.exists()
    assert manifest_module.load(cfg) == {}


def test_uninstalling_a_group_restores_every_displaced_vendor_page(
    tmp_path: Path,
) -> None:
    cfg = _eza_config(tmp_path)
    installed = _install_eza_release(tmp_path, cfg, vendor_pages=True)

    result = uninstall_manpage("eza_colors-explanation", config=cfg)

    for name, path in installed.items():
        assert path not in result.removed
        assert path.read_text(encoding="utf-8") == f"vendor {path.name}\n"
        assert not (cfg.backup_dir / path.name).exists(), name
    assert manifest_module.load(cfg) == {}


def test_uninstalling_a_group_keeps_a_member_whose_bytes_changed(
    tmp_path: Path,
) -> None:
    """Checksum protection is per member: one edited page stays, the rest go."""
    cfg = _eza_config(tmp_path)
    installed = _install_eza_release(tmp_path, cfg)
    edited = manifest_module.lookup("eza_colors", config=cfg)
    assert edited is not None and edited.target is not None
    edited.target.write_text(".TH EZA_COLORS 5 edited elsewhere\n", encoding="utf-8")

    result = uninstall_manpage("eza", config=cfg)

    assert result.modified_kept == [installed["eza_colors"]]
    assert installed["eza_colors"].exists()
    assert manifest_module.lookup("eza_colors", config=cfg) is not None
    assert not installed["eza"].exists()
    assert not installed["eza_colors-explanation"].exists()
    assert manifest_module.lookup("eza", config=cfg) is None


def test_uninstalling_an_ungrouped_entry_touches_only_itself(tmp_path: Path) -> None:
    """A page recorded with no group uninstalls alone, unchanged."""
    cfg = _eza_config(tmp_path)
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    for name in ("alpha.1", "beta.1"):
        source = source_dir / name
        source.write_text(f".TH {name}\n", encoding="utf-8")
        install_manpage(
            source,
            Path(name).stem,
            Tier.REPOSITORY,
            "owner/repo",
            config=cfg,
        )

    result = uninstall_manpage("alpha", config=cfg)

    assert cfg.man_dir / "alpha.1" in result.removed
    assert (cfg.man_dir / "beta.1").exists()
    assert manifest_module.lookup("beta", config=cfg) is not None


def _legacy_targetless_entry(tmp_path: Path) -> tuple[Config, Path]:
    """Record a pre-ADR-0028 entry with no recorded target, matching bytes."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    installed = man_dir / "tool.1"
    installed.write_text(".TH TOOL 1 legacy", encoding="utf-8")
    cfg = Config(
        man_dir=man_dir,
        output_dir=tmp_path / "durable",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    record_entry(
        "tool",
        installed,
        Tier.SYNTHESIS,
        "model",
        manifest_module.checksum_of(installed),
        config=cfg,
    )
    return cfg, installed


def test_uninstall_keeps_a_legacy_targetless_entry(tmp_path: Path) -> None:
    """Without --force a targetless entry stays, reported as legacy, not modified."""
    cfg, installed = _legacy_targetless_entry(tmp_path)

    result = uninstall_manpage("tool", config=cfg)

    assert result.legacy_kept == [installed]
    assert result.modified_kept == []
    assert result.foreign_kept is None
    assert installed.exists()
    assert manifest_module.lookup("tool", config=cfg) is not None


def test_uninstall_force_removes_a_legacy_targetless_entry(tmp_path: Path) -> None:
    """--force reaches a targetless entry; checking `target` first made it unremovable."""
    cfg, installed = _legacy_targetless_entry(tmp_path)

    result = uninstall_manpage("tool", force=True, config=cfg)

    assert result.legacy_kept == []
    assert result.modified_kept == []
    assert installed in result.removed
    assert not installed.exists()
    assert manifest_module.lookup("tool", config=cfg) is None


def test_install_refuses_to_clobber_another_entrys_durable_target(
    tmp_path: Path,
) -> None:
    """Two tools whose pages share a basename resolve to one durable target.

    Writing the second over the first replaces bytes the first entry's
    checksum still records, which leaves that entry permanently
    un-uninstallable. The install fails instead, naming the owner.
    """
    cfg = Config(
        man_dir=tmp_path / "man1",
        output_dir=tmp_path / "durable",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    first_source = tmp_path / "first" / "page.1"
    first_source.parent.mkdir()
    first_source.write_text(".TH PAGE 1 first", encoding="utf-8")
    second_source = tmp_path / "second" / "page.1"
    second_source.parent.mkdir()
    second_source.write_text(".TH PAGE 1 second", encoding="utf-8")

    install_manpage(
        first_source,
        "first",
        Tier.SYNTHESIS,
        "model",
        target_dir=tmp_path / "man1",
        config=cfg,
    )
    first_entry = manifest_module.lookup("first", config=cfg)
    assert first_entry is not None and first_entry.target is not None

    with pytest.raises(FileExistsError, match="already recorded by 'first'"):
        install_manpage(
            second_source,
            "second",
            Tier.SYNTHESIS,
            "model",
            target_dir=tmp_path / "man2",
            config=cfg,
        )

    assert first_entry.target.read_text(encoding="utf-8") == ".TH PAGE 1 first"
    assert manifest_module.lookup("second", config=cfg) is None
    assert manifest_module.lookup("first", config=cfg) == first_entry


def test_reinstalling_a_tool_reuses_its_own_durable_target(tmp_path: Path) -> None:
    """The collision guard must not fire on an entry's own recorded target."""
    cfg = Config(
        man_dir=tmp_path / "man1",
        output_dir=tmp_path / "durable",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    source = tmp_path / "source" / "tool.1"
    source.parent.mkdir()
    source.write_text(".TH TOOL 1 first", encoding="utf-8")
    install_manpage(source, "tool", Tier.SYNTHESIS, "model", config=cfg)

    source.write_text(".TH TOOL 1 second", encoding="utf-8")
    install_manpage(source, "tool", Tier.SYNTHESIS, "model", config=cfg)

    entry = manifest_module.lookup("tool", config=cfg)
    assert entry is not None and entry.target is not None
    assert entry.target.read_text(encoding="utf-8") == ".TH TOOL 1 second"


def test_uninstall_keeps_a_durable_target_a_second_entry_still_records(
    tmp_path: Path,
) -> None:
    """A manifest written before the collision guard can share one durable target.

    Uninstalling one user must leave the other's link resolvable: deleting
    the target dangles it and makes that entry un-uninstallable in turn.
    """
    cfg = Config(
        man_dir=tmp_path / "man1",
        output_dir=tmp_path / "durable",
        manifest_path=tmp_path / "state" / "installed.json",
    )
    target = cfg.output_dir / "page.1"
    target.parent.mkdir(parents=True)
    target.write_text(".TH PAGE 1 shared", encoding="utf-8")
    checksum = manifest_module.checksum_of(target)
    links = {}
    # Recorded before the links exist: an unrecorded link into `output_dir` is
    # an orphan, and a transaction adopts it under its own filename.
    for tool, man_dir in (("first", tmp_path / "man1"), ("second", tmp_path / "man2")):
        man_dir.mkdir(parents=True)
        links[tool] = man_dir / "page.1"
        record_entry(
            tool,
            links[tool],
            Tier.SYNTHESIS,
            "model",
            checksum,
            target=target,
            config=cfg,
        )
    for link in links.values():
        link.symlink_to(target)

    first = uninstall_manpage("first", config=cfg)

    assert links["first"] in first.removed
    assert not links["first"].exists()
    assert target not in first.removed
    assert target.exists()
    assert links["second"].resolve() == target
    assert manifest_module.lookup("second", config=cfg) is not None

    second = uninstall_manpage("second", config=cfg)

    assert links["second"] in second.removed
    assert target in second.removed
    assert not target.exists()
    assert manifest_module.lookup("second", config=cfg) is None


def _crash_config(tmp_path: Path) -> Config:
    return Config(
        man_dir=tmp_path / "man1",
        output_dir=tmp_path / "durable",
        backup_dir=tmp_path / "backup",
        manifest_path=tmp_path / "state" / "installed.json",
    )


def _source_page(tmp_path: Path, text: str = ".TH TOOL 1 upstream") -> Path:
    source = tmp_path / "cache" / "tool.1"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(text, encoding="utf-8")
    return source


def _vendor_page(cfg: Config, text: str = ".TH TOOL 1 vendor") -> Path:
    cfg.man_dir.mkdir(parents=True, exist_ok=True)
    page = cfg.man_dir / "tool.1"
    page.write_text(text, encoding="utf-8")
    return page


def test_install_interrupted_before_materialize_leaves_no_backup_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A displaced page not displaced after all must not leave a stray backup.

    The backup would otherwise sit in `backup_dir` named after a page that
    was never replaced, and a later install of the same tool would carry it
    forward as the vendor page to restore.
    """
    cfg = _crash_config(tmp_path)
    source = _source_page(tmp_path)
    vendor = _vendor_page(cfg)
    monkeypatch.setattr(
        "maniac.lifecycle.materialize_target",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("no space")),
    )

    with pytest.raises(OSError):
        install_manpage(
            source, "tool", Tier.REPOSITORY, "owner/tool", force=True, config=cfg
        )

    assert vendor.read_text(encoding="utf-8") == ".TH TOOL 1 vendor"
    assert list(cfg.backup_dir.glob("*")) == []
    assert manifest_module.load(config=cfg) == {}

    monkeypatch.undo()
    installed = install_manpage(
        source, "tool", Tier.REPOSITORY, "owner/tool", force=True, config=cfg
    )
    entry = manifest_module.lookup("tool", config=cfg)
    assert entry is not None and entry.backup == cfg.backup_dir / "tool.1"
    assert installed.is_symlink()


def test_install_interrupted_before_linking_leaves_no_orphan_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A materialized target nothing links to is unreachable litter under output_dir."""
    cfg = _crash_config(tmp_path)
    source = _source_page(tmp_path)
    monkeypatch.setattr(
        "maniac.lifecycle.link_manpath_entry",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("read-only manpath")),
    )

    with pytest.raises(OSError):
        install_manpage(source, "tool", Tier.REPOSITORY, "owner/tool", config=cfg)

    assert list(cfg.output_dir.glob("*")) == []
    assert manifest_module.load(config=cfg) == {}

    monkeypatch.undo()
    installed = install_manpage(
        source, "tool", Tier.REPOSITORY, "owner/tool", config=cfg
    )
    assert installed.is_symlink()
    assert (cfg.output_dir / "tool.1").exists()


def test_install_interrupted_before_linking_keeps_the_synthesized_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tier 3 installs from its own durable target; rollback must not delete it.

    Synthesis compiles straight into `output_dir` and hands that path in as
    the source, so nothing is copied and the page predates the install.
    Removing it on a failed link throws away a paid LLM call.
    """
    cfg = _crash_config(tmp_path)
    cfg.output_dir.mkdir(parents=True)
    synthesized = cfg.output_dir / "tool.1"
    synthesized.write_text(".TH TOOL 1 synthesized", encoding="utf-8")
    monkeypatch.setattr(
        "maniac.lifecycle.link_manpath_entry",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("read-only manpath")),
    )

    with pytest.raises(OSError):
        install_manpage(synthesized, "tool", Tier.SYNTHESIS, "model", config=cfg)

    assert synthesized.read_text(encoding="utf-8") == ".TH TOOL 1 synthesized"
    assert manifest_module.load(config=cfg) == {}


def test_install_interrupted_before_linking_restores_the_displaced_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The vendor page the failed install displaced must still be on the manpath."""
    cfg = _crash_config(tmp_path)
    source = _source_page(tmp_path)
    vendor = _vendor_page(cfg)

    def unlink_then_fail(path: Path, target: Path) -> None:
        path.unlink()
        raise OSError("interrupted after replacing the manpath entry")

    monkeypatch.setattr("maniac.lifecycle.link_manpath_entry", unlink_then_fail)

    with pytest.raises(OSError):
        install_manpage(
            source, "tool", Tier.REPOSITORY, "owner/tool", force=True, config=cfg
        )

    assert vendor.read_text(encoding="utf-8") == ".TH TOOL 1 vendor"
    assert list(cfg.backup_dir.glob("*")) == []
    assert manifest_module.load(config=cfg) == {}


def test_uninstall_interrupted_before_the_manifest_write_reruns_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page goes before the record does; a rerun must finish the job."""
    cfg = _crash_config(tmp_path)
    source = _source_page(tmp_path)
    installed = install_manpage(
        source, "tool", Tier.REPOSITORY, "owner/tool", config=cfg
    )
    monkeypatch.setattr(
        "maniac.manifest.save",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("interrupted")),
    )

    with pytest.raises(OSError):
        uninstall_manpage("tool", config=cfg)

    assert not installed.exists()
    assert manifest_module.lookup("tool", config=cfg) is not None

    monkeypatch.undo()
    result = uninstall_manpage("tool", config=cfg)

    assert result.modified_kept == [] and result.legacy_kept == []
    assert manifest_module.load(config=cfg) == {}
    assert list(cfg.output_dir.glob("*.1")) == []


def test_install_interrupted_before_the_manifest_write_reruns_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The link lands before the record; the rerun must not call MANIAC's own page foreign.

    The orphaned link is adopted on the next transaction, so reinstalling
    the same tool overwrites its own page instead of demanding --force.
    """
    cfg = _crash_config(tmp_path)
    source = _source_page(tmp_path)
    # An unrelated page first, so the manifest the rerun reads is intact and
    # the orphan is adopted rather than reconstructed by recovery.
    other = tmp_path / "cache" / "other.1"
    other.write_text(".TH OTHER 1", encoding="utf-8")
    install_manpage(other, "other", Tier.REPOSITORY, "owner/other", config=cfg)
    monkeypatch.setattr(
        "maniac.manifest.save",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("interrupted")),
    )

    with pytest.raises(OSError):
        install_manpage(source, "tool", Tier.REPOSITORY, "owner/tool", config=cfg)

    installed = cfg.man_dir / "tool.1"
    assert installed.is_symlink()
    assert set(manifest_module.load(config=cfg)) == {"other"}

    monkeypatch.undo()
    reinstalled = install_manpage(
        source, "tool", Tier.REPOSITORY, "owner/tool", config=cfg
    )

    assert reinstalled == installed
    entry = manifest_module.lookup("tool", config=cfg)
    assert entry is not None
    assert entry.tier is Tier.REPOSITORY
    assert entry.backup is None


def test_an_adopted_orphan_does_not_become_a_second_owner_of_one_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adoption keys on the filename; the install that follows keys on the tool.

    Both name one manpath path, and two owners of one page make the second
    an entry whose page vanishes the moment the first is uninstalled.
    """
    cfg = _crash_config(tmp_path)
    source = tmp_path / "cache" / "page.1"
    source.parent.mkdir(parents=True)
    source.write_text(".TH PAGE 1", encoding="utf-8")
    other = tmp_path / "cache" / "other.1"
    other.write_text(".TH OTHER 1", encoding="utf-8")
    install_manpage(other, "other", Tier.REPOSITORY, "owner/other", config=cfg)
    monkeypatch.setattr(
        "maniac.manifest.save",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        install_manpage(source, "mytool", Tier.REPOSITORY, "owner/mytool", config=cfg)
    monkeypatch.undo()

    installed = install_manpage(
        source, "mytool", Tier.REPOSITORY, "owner/mytool", config=cfg
    )

    entries = manifest_module.load(config=cfg)
    assert set(entries) == {"other", "mytool"}
    assert entries["mytool"].path == installed
