import json
from pathlib import Path

import pytest

from maniac import manifest
from maniac.config import Config
from maniac.generation.compiler import build_provenance_header
from maniac.manifest import Tier


def _config(tmp_path: Path) -> Config:
    return Config(manifest_path=tmp_path / "state" / "installed.json")


@pytest.fixture(autouse=True)
def _isolated_xdg_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep default Config instances away from a developer's MANIAC state."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


def test_load_with_no_manifest_and_no_pages_seeds_empty(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    assert manifest.load(config=cfg) == {}
    assert cfg.manifest_path.exists()


def test_record_lookup_forget_round_trip(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    page = tmp_path / "man1" / "tool.1"

    manifest.record(
        "tool", page, Tier.INSTALL_ROOT, "install-root-source", "abc123", config=cfg
    )
    entry = manifest.lookup("tool", config=cfg)
    assert entry is not None
    assert entry.path == page
    assert entry.tier is Tier.INSTALL_ROOT
    assert entry.source == "install-root-source"
    assert entry.checksum == "abc123"

    manifest.forget("tool", config=cfg)
    assert manifest.lookup("tool", config=cfg) is None


def test_record_lookup_round_trip_preserves_version(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    page = tmp_path / "man1" / "tool.1"

    manifest.record(
        "tool",
        page,
        Tier.INSTALL_ROOT,
        "install-root-source",
        "abc123",
        config=cfg,
        version="1.2.3",
    )
    entry = manifest.lookup("tool", config=cfg)
    assert entry is not None
    assert entry.version == "1.2.3"


def test_record_lookup_round_trip_preserves_source_uri(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    uri = "https://github.com/owner/tool/blob/v1.2.3/man/tool.1"

    manifest.record(
        "tool",
        tmp_path / "man1" / "tool.1",
        Tier.REPOSITORY,
        "owner/tool",
        "abc123",
        config=cfg,
        source_uri=uri,
    )

    entry = manifest.lookup("tool", config=cfg)
    assert entry is not None
    assert entry.source_uri == uri


def test_record_lookup_round_trip_preserves_link_target(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    target = tmp_path / "data" / "tool.1"

    manifest.record(
        "tool",
        tmp_path / "man1" / "tool.1",
        Tier.SYNTHESIS,
        "model",
        "abc123",
        target=target,
        config=cfg,
    )

    entry = manifest.lookup("tool", config=cfg)
    assert entry is not None
    assert entry.target == target


def test_row_missing_version_key_loads_as_none(tmp_path: Path) -> None:
    """A row written before the field existed reads version=None, not dropped or raised (ADR-0018)."""
    cfg = _config(tmp_path)
    cfg.manifest_path.parent.mkdir(parents=True)
    cfg.manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    "tool": {
                        "path": "/x/tool.1",
                        "tier": "synthesis",
                        "source": "m",
                        "checksum": "abc123",
                        "backup": None,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    entries = manifest.load(config=cfg)
    assert set(entries) == {"tool"}
    assert entries["tool"].version is None


def test_malformed_source_uri_is_ignored(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.manifest_path.parent.mkdir(parents=True)
    cfg.manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    "tool": {
                        "path": "/x/tool.1",
                        "tier": "repository",
                        "source": "owner/tool",
                        "checksum": "abc123",
                        "backup": None,
                        "source_uri": 42,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    entry = manifest.load(config=cfg)["tool"]

    assert entry.source_uri is None


def test_forget_missing_tool_is_a_noop(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    manifest.forget("nonexistent", config=cfg)  # must not raise
    assert manifest.lookup("nonexistent", config=cfg) is None


def test_record_persists_across_loads(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    manifest.record(
        "tool", tmp_path / "tool.1", Tier.SYNTHESIS, "model", "abc123", config=cfg
    )

    document = json.loads(cfg.manifest_path.read_text(encoding="utf-8"))
    assert document["version"] == 1
    assert document["entries"]["tool"]["tier"] == "synthesis"


def test_corrupt_manifest_degrades_to_empty(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.manifest_path.parent.mkdir(parents=True)
    cfg.manifest_path.write_text("{not json", encoding="utf-8")

    assert manifest.load(config=cfg) == {}


def test_wrong_version_degrades_to_empty(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.manifest_path.parent.mkdir(parents=True)
    cfg.manifest_path.write_text(
        json.dumps({"version": 999, "entries": {}}), encoding="utf-8"
    )

    assert manifest.load(config=cfg) == {}


def test_one_malformed_entry_is_skipped_not_the_whole_file(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.manifest_path.parent.mkdir(parents=True)
    cfg.manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    "good": {
                        "path": "/x/good.1",
                        "tier": "synthesis",
                        "source": "m",
                        "checksum": "abc123",
                        "backup": None,
                    },
                    "bad_tier": {
                        "path": "/x/bad.1",
                        "tier": "not-a-real-tier",
                        "source": "m",
                        "checksum": "abc123",
                        "backup": None,
                    },
                    "missing_field": {"path": "/x/missing.1", "tier": "synthesis"},
                },
            }
        ),
        encoding="utf-8",
    )

    entries = manifest.load(config=cfg)
    assert set(entries) == {"good"}


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

    assert manifest.load(config=full_cfg) == {}


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
    assert manifest.load(config=cfg) == {}


def test_migration_seeds_from_a_header_carrying_page(tmp_path: Path) -> None:
    """No manifest file yet: a synthesized page's provenance header seeds tier=synthesis."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    header = build_provenance_header("tool", model="Gemini 3.7 Flash")
    (man_dir / "tool.1").write_text(header + ".TH TOOL 1", encoding="utf-8")

    cfg = Config(manifest_path=tmp_path / "state" / "installed.json", man_dir=man_dir)
    entries = manifest.load(config=cfg)

    assert set(entries) == {"tool"}
    assert entries["tool"].tier is Tier.SYNTHESIS
    assert entries["tool"].source == "Gemini 3.7 Flash"
    assert entries["tool"].path == man_dir / "tool.1"
    assert entries["tool"].checksum == manifest.checksum_of(man_dir / "tool.1")
    # Migration persists so it never reruns.
    assert cfg.manifest_path.exists()


def test_migration_does_not_recover_headerless_tier1_pages(tmp_path: Path) -> None:
    """Tier-1/2 pages carry nothing recoverable -- accepted cost of the migration (ADR-0017)."""
    man_dir = tmp_path / "man1"
    man_dir.mkdir(parents=True)
    (man_dir / "vendor.1").write_text(".TH VENDOR 1 no header", encoding="utf-8")

    cfg = Config(manifest_path=tmp_path / "state" / "installed.json", man_dir=man_dir)
    assert manifest.load(config=cfg) == {}


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
    manifest.record(
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

    entry = manifest.load(config=cfg)["tool"]

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
    manifest.record(
        "tool", page, Tier.SYNTHESIS, "model", manifest.checksum_of(page), config=cfg
    )
    page.write_text("user changed", encoding="utf-8")

    entry = manifest.load(config=cfg)["tool"]

    assert not page.is_symlink()
    assert page.read_text(encoding="utf-8") == "user changed"
    assert entry.target is None
    assert not cfg.output_dir.exists()


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
    entries = manifest.load(config=cfg)

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
    manifest.load(config=cfg)

    assert stray_backup.exists()
