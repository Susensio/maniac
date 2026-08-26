from pathlib import Path

import pytest

from maniac.config import Config
from maniac.generation.compiler import build_provenance_header
from maniac.installer import (
    install_manpage,
    list_installed_manpages,
    read_provenance_header,
    uninstall_manpage,
)


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
    header = build_provenance_header(tool_name="tool")
    src_file.write_text(header + ".TH TOOL 1", encoding="utf-8")

    target_dir = tmp_path / "man1"
    installed = install_manpage(src_file, target_dir=target_dir)

    assert installed == target_dir / "tool.1"
    assert installed.exists()


def test_install_manpage_maniac_overwrite(tmp_path: Path) -> None:
    target_dir = tmp_path / "man1"
    target_dir.mkdir(parents=True)
    existing_dest = target_dir / "tool.1"
    header1 = build_provenance_header(tool_name="tool", model="old_model")
    existing_dest.write_text(header1 + ".TH TOOL 1 old", encoding="utf-8")

    src_file = tmp_path / "src" / "tool.1"
    src_file.parent.mkdir(parents=True)
    header2 = build_provenance_header(tool_name="tool", model="new_model")
    src_file.write_text(header2 + ".TH TOOL 1 new", encoding="utf-8")

    installed = install_manpage(src_file, target_dir=target_dir, force=False)
    assert installed.exists()
    assert "new_model" in installed.read_text(encoding="utf-8")


def test_install_manpage_foreign_without_force_fails(tmp_path: Path) -> None:
    target_dir = tmp_path / "man1"
    target_dir.mkdir(parents=True)
    existing_dest = target_dir / "tool.1"
    existing_dest.write_text(".TH TOOL 1 Official vendor doc", encoding="utf-8")

    src_file = tmp_path / "src" / "tool.1"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(
        build_provenance_header("tool") + ".TH TOOL 1", encoding="utf-8"
    )

    with pytest.raises(FileExistsError) as exc_info:
        install_manpage(src_file, target_dir=target_dir, force=False)

    assert "foreign or vendor manpage already exists" in str(exc_info.value)
    assert existing_dest.read_text(encoding="utf-8") == ".TH TOOL 1 Official vendor doc"


def test_install_manpage_foreign_with_force_creates_backup(tmp_path: Path) -> None:
    target_dir = tmp_path / "man1"
    target_dir.mkdir(parents=True)
    existing_dest = target_dir / "tool.1"
    existing_dest.write_text(".TH TOOL 1 Official vendor doc", encoding="utf-8")

    src_file = tmp_path / "src" / "tool.1"
    src_file.parent.mkdir(parents=True)
    header = build_provenance_header("tool")
    src_file.write_text(header + ".TH TOOL 1 maniac", encoding="utf-8")

    installed = install_manpage(src_file, target_dir=target_dir, force=True)
    assert installed.exists()
    assert "maniac" in installed.read_text(encoding="utf-8")

    backup_file = target_dir / "tool.1.maniac_bak"
    assert backup_file.exists()
    assert backup_file.read_text(encoding="utf-8") == ".TH TOOL 1 Official vendor doc"


def test_uninstall_manpage_and_restore_backup(tmp_path: Path) -> None:
    """M3: a file restored from backup is not reported as removed -- it still exists."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)

    installed_file = man_dir / "tool.1"
    header = build_provenance_header("tool")
    installed_file.write_text(header + ".TH TOOL 1 maniac", encoding="utf-8")

    backup_file = man_dir / "tool.1.maniac_bak"
    backup_file.write_text(".TH TOOL 1 Official vendor doc", encoding="utf-8")

    cfg = Config(man_dir=man_dir, output_dir=tmp_path / "data_manpages")
    result = uninstall_manpage("tool", purge=False, config=cfg)

    assert installed_file not in result.removed
    assert installed_file.exists()  # Restored from backup!
    assert (
        installed_file.read_text(encoding="utf-8") == ".TH TOOL 1 Official vendor doc"
    )
    assert not backup_file.exists()


def test_uninstall_manpage_purge(tmp_path: Path) -> None:
    """M11: purge also removes the orphaned `{tool}_prompt.md` intermediate file."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    out_dir = tmp_path / "data_manpages"
    out_dir.mkdir(parents=True)
    inter_dir = tmp_path / "intermediate"
    inter_dir.mkdir(parents=True)

    installed_file = man_dir / "tool.1"
    header = build_provenance_header("tool")
    installed_file.write_text(header + ".TH TOOL 1 maniac", encoding="utf-8")

    md_file = out_dir / "tool.1.md"
    md_file.write_text("# TOOL", encoding="utf-8")

    ctx_file = inter_dir / "tool_context.md"
    ctx_file.write_text("Context", encoding="utf-8")

    prompt_file = inter_dir / "tool_prompt.md"
    prompt_file.write_text("Prompt", encoding="utf-8")

    cfg = Config(man_dir=man_dir, output_dir=out_dir, intermediate_dir=inter_dir)
    result = uninstall_manpage("tool", purge=True, config=cfg)

    assert installed_file in result.removed
    assert not installed_file.exists()
    assert not md_file.exists()
    assert not ctx_file.exists()
    assert prompt_file in result.removed
    assert not prompt_file.exists()


def test_uninstall_manpage_foreign_kept(tmp_path: Path) -> None:
    """M2: a foreign vendor page in man_dir is left untouched and reported as such."""
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
    assert foreign_file.exists()  # untouched
    assert stored_roff in result.removed
    assert not stored_roff.exists()


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
    assert items[0]["has_backup"] is False
