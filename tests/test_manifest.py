import json
from pathlib import Path

import pytest

from maniac import manifest
from maniac.config import Config
from maniac.manifest import Tier


def _config(tmp_path: Path) -> Config:
    return Config(manifest_path=tmp_path / "state" / "installed.json")


@pytest.fixture(autouse=True)
def _isolated_xdg_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep default Config instances away from a developer's MANIAC state."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


def test_load_with_no_manifest_reads_empty_and_writes_nothing(tmp_path: Path) -> None:
    """Load is deserialization only: an absent manifest is not seeded here."""
    cfg = _config(tmp_path)
    assert manifest.load(config=cfg) == {}
    assert not cfg.manifest_path.exists()


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


def test_load_does_not_migrate_a_legacy_entry(tmp_path: Path) -> None:
    """The core read-only invariant: deserializing a migration-eligible entry writes nothing.

    A pre-ADR-0028 copy is exactly what `lifecycle.reconcile` converts into an
    owned link. `lookup()` from `list` must observe it as persisted instead.
    """
    man_dir = tmp_path / "man1"
    man_dir.mkdir()
    page = man_dir / "tool.1"
    page.write_bytes(b"legacy bytes")
    cfg = Config(
        manifest_path=tmp_path / "state" / "installed.json",
        man_dir=man_dir,
        output_dir=tmp_path / "data",
    )
    manifest.record(
        "tool", page, Tier.SYNTHESIS, "model", manifest.checksum_of(page), config=cfg
    )
    before = _tree_state(tmp_path)

    entry = manifest.lookup("tool", config=cfg)

    assert entry is not None
    assert entry.target is None
    assert not page.is_symlink()
    assert page.read_bytes() == b"legacy bytes"
    assert not cfg.output_dir.exists()
    assert _tree_state(tmp_path) == before


def _tree_state(root: Path) -> dict[str, tuple[float, int]]:
    """Every file below `root` mapped to its mtime and size."""
    return {
        str(path.relative_to(root)): (
            path.lstat().st_mtime,
            path.lstat().st_size,
        )
        for path in sorted(root.rglob("*"))
    }
