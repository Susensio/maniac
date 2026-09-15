import json
import multiprocessing
import time
from pathlib import Path

import pytest

from maniac import manifest
from maniac.config import Config
from maniac.manifest import Tier

from .manifest_support import forget_entry, record_entry


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

    record_entry(
        "tool", page, Tier.INSTALL_ROOT, "install-root-source", "abc123", config=cfg
    )
    entry = manifest.lookup("tool", config=cfg)
    assert entry is not None
    assert entry.path == page
    assert entry.tier is Tier.INSTALL_ROOT
    assert entry.source == "install-root-source"
    assert entry.checksum == "abc123"

    forget_entry("tool", config=cfg)
    assert manifest.lookup("tool", config=cfg) is None


def test_record_lookup_round_trip_preserves_version(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    page = tmp_path / "man1" / "tool.1"

    record_entry(
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

    record_entry(
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

    record_entry(
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
    forget_entry("nonexistent", config=cfg)  # must not raise
    assert manifest.lookup("nonexistent", config=cfg) is None


def test_record_persists_across_loads(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    record_entry(
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


def test_newer_schema_version_refuses_as_damaged_not_as_empty(tmp_path: Path) -> None:
    """A manifest this build cannot read is the one refusal case, and it says so."""
    cfg = _config(tmp_path)
    cfg.manifest_path.parent.mkdir(parents=True)
    cfg.manifest_path.write_text(
        json.dumps({"version": manifest.SCHEMA_VERSION + 1, "entries": {}}),
        encoding="utf-8",
    )

    result = manifest.read(config=cfg)
    assert result.health is manifest.Health.DAMAGED
    assert result.reason is not None
    assert "newer" in result.reason


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
    record_entry(
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


def test_record_lookup_round_trip_preserves_group(tmp_path: Path) -> None:
    cfg = _config(tmp_path)

    record_entry(
        "eza_colors",
        tmp_path / "man5" / "eza_colors.5",
        Tier.REPOSITORY,
        "eza-community/eza",
        "abc123",
        config=cfg,
        group="eza",
    )

    entry = manifest.lookup("eza_colors", config=cfg)
    assert entry is not None
    assert entry.group == "eza"


def test_row_missing_group_key_loads_as_ungrouped(tmp_path: Path) -> None:
    """A single-page row written before groups existed reads group=None (ADR-0018)."""
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
                        "version": "1.2.3",
                        "target": "/durable/tool.1",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    entry = manifest.lookup("tool", config=cfg)
    assert entry is not None
    assert entry.group is None
    assert entry.version == "1.2.3"
    assert entry.target == Path("/durable/tool.1")


def _write_manifest(cfg: Config, document: object) -> None:
    cfg.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.manifest_path.write_text(json.dumps(document), encoding="utf-8")


_GOOD_ROW = {
    "path": "/x/good.1",
    "tier": "synthesis",
    "source": "m",
    "checksum": "abc123",
    "backup": None,
}


def test_read_of_absent_manifest_is_absent_not_damaged(tmp_path: Path) -> None:
    result = manifest.read(config=_config(tmp_path))

    assert result.health is manifest.Health.ABSENT
    assert result.entries == {}
    assert result.reason is None
    assert result.lost == {}


def test_read_of_a_whole_manifest_is_intact(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _write_manifest(cfg, {"version": 1, "entries": {"good": _GOOD_ROW}})

    result = manifest.read(config=cfg)

    assert result.health is manifest.Health.INTACT
    assert set(result.entries) == {"good"}
    assert result.lost == {}


def test_read_of_corrupt_json_is_damaged(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.manifest_path.parent.mkdir(parents=True)
    cfg.manifest_path.write_text("{not json", encoding="utf-8")

    result = manifest.read(config=cfg)

    assert result.health is manifest.Health.DAMAGED
    assert result.entries == {}
    assert result.reason is not None


def test_one_skipped_row_makes_the_read_damaged_and_names_the_lost_key(
    tmp_path: Path,
) -> None:
    """Partial rot is the likely case: the salvage must still be reported as a loss."""
    cfg = _config(tmp_path)
    _write_manifest(
        cfg,
        {
            "version": 1,
            "entries": {
                "good": _GOOD_ROW,
                "bad_tier": {**_GOOD_ROW, "tier": "not-a-real-tier"},
            },
        },
    )

    result = manifest.read(config=cfg)

    assert result.health is manifest.Health.DAMAGED
    assert set(result.entries) == {"good"}
    assert set(result.lost) == {"bad_tier"}
    assert "not-a-real-tier" in result.lost["bad_tier"]


def test_older_schema_version_reads_fine(tmp_path: Path) -> None:
    """The version is a floor: anything at or below it is readable."""
    cfg = _config(tmp_path)
    _write_manifest(cfg, {"version": 0, "entries": {"good": _GOOD_ROW}})

    result = manifest.read(config=cfg)

    assert result.health is manifest.Health.INTACT
    assert set(result.entries) == {"good"}


def test_intact_read_promotes_the_checkpoint(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _write_manifest(cfg, {"version": 1, "entries": {"good": _GOOD_ROW}})

    assert manifest.promote(manifest.read(config=cfg), config=cfg) is True

    checkpoint = manifest.checkpoint_path(cfg)
    assert checkpoint == cfg.manifest_path.with_name("installed.json.good")
    assert manifest.read(config=Config(manifest_path=checkpoint)).entries == {
        "good": manifest.lookup("good", config=cfg)
    }


def test_damaged_read_leaves_an_existing_checkpoint_byte_identical(
    tmp_path: Path,
) -> None:
    """The refusal to promote is the mechanism: rot must not overwrite the good copy."""
    cfg = _config(tmp_path)
    _write_manifest(cfg, {"version": 1, "entries": {"good": _GOOD_ROW}})
    manifest.promote(manifest.read(config=cfg), config=cfg)
    checkpoint = manifest.checkpoint_path(cfg)
    before = checkpoint.read_bytes()

    _write_manifest(
        cfg,
        {
            "version": 1,
            "entries": {"bad_tier": {**_GOOD_ROW, "tier": "not-a-real-tier"}},
        },
    )
    assert manifest.promote(manifest.read(config=cfg), config=cfg) is False

    assert checkpoint.read_bytes() == before


def test_record_promotes_the_manifest_it_read_before_mutating_it(
    tmp_path: Path,
) -> None:
    """The checkpoint holds the generation before the write, not the one after."""
    cfg = _config(tmp_path)
    record_entry("first", tmp_path / "first.1", Tier.SYNTHESIS, "m", "a", config=cfg)
    record_entry("second", tmp_path / "sec.1", Tier.SYNTHESIS, "m", "b", config=cfg)

    checkpointed = manifest.read(
        config=Config(manifest_path=manifest.checkpoint_path(cfg))
    )
    assert set(checkpointed.entries) == {"first"}
    assert set(manifest.load(config=cfg)) == {"first", "second"}


def test_read_of_a_damaged_manifest_writes_nothing(tmp_path: Path) -> None:
    """Load stays pure deserialization (ADR-0034), damaged or not."""
    cfg = _config(tmp_path)
    _write_manifest(
        cfg,
        {
            "version": 1,
            "entries": {
                "good": _GOOD_ROW,
                "bad_tier": {**_GOOD_ROW, "tier": "not-a-real-tier"},
            },
        },
    )
    before = _tree_state(tmp_path)

    result = manifest.read(config=cfg)

    assert result.health is manifest.Health.DAMAGED
    assert _tree_state(tmp_path) == before


def _write_holding_the_lock(manifest_path: str, tool: str, hold: float) -> None:
    """Take the manifest lock in this process, dawdle inside it, then write."""
    cfg = Config(manifest_path=Path(manifest_path))
    with manifest.transaction(config=cfg) as txn:
        time.sleep(hold)
        txn.put(
            tool,
            manifest.Entry(
                path=Path(f"/x/{tool}.1"),
                tier=Tier.SYNTHESIS,
                source="m",
                checksum="abc123",
            ),
        )


def test_two_concurrent_writers_neither_loses_the_other_entry(tmp_path: Path) -> None:
    """The whole point of the lock: an unserialized read-modify-save drops a writer.

    Both processes write the whole document, so the loser of a race silently
    replaces the winner's entry with a manifest that never held it.
    """
    cfg = _config(tmp_path)
    other = multiprocessing.get_context("fork").Process(
        target=_write_holding_the_lock, args=(str(cfg.manifest_path), "held", 1.0)
    )
    other.start()
    try:
        time.sleep(0.3)
        _write_holding_the_lock(str(cfg.manifest_path), "waited", 0.0)
    finally:
        other.join(10)

    assert other.exitcode == 0
    assert set(manifest.load(config=cfg)) == {"held", "waited"}


def test_a_transaction_writes_once_for_every_mutation_it_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One operation is one write: a three-page release lands whole or not at all."""
    cfg = _config(tmp_path)
    writes: list[dict[str, manifest.Entry]] = []
    real_save = manifest.save

    def counting_save(
        entries: dict[str, manifest.Entry], config: Config | None = None
    ) -> None:
        writes.append(dict(entries))
        real_save(entries, config)

    monkeypatch.setattr(manifest, "save", counting_save)

    with manifest.transaction(config=cfg) as txn:
        for page in ("eza", "eza_colors", "eza_colors-explanation"):
            txn.put(
                page,
                manifest.Entry(
                    path=Path(f"/x/{page}.1"),
                    tier=Tier.REPOSITORY,
                    source="eza-community/eza",
                    checksum="abc123",
                    group="eza",
                ),
            )
        assert writes == []

    assert len(writes) == 1
    assert set(manifest.load(config=cfg)) == {
        "eza",
        "eza_colors",
        "eza_colors-explanation",
    }


def test_a_failed_transaction_writes_nothing(tmp_path: Path) -> None:
    """An operation that raises leaves the manifest describing where it started."""
    cfg = _config(tmp_path)
    record_entry("first", tmp_path / "first.1", Tier.SYNTHESIS, "m", "a", config=cfg)
    before = cfg.manifest_path.read_bytes()

    with pytest.raises(RuntimeError), manifest.transaction(config=cfg) as txn:
        txn.forget("first")
        txn.put(
            "second",
            manifest.Entry(
                path=tmp_path / "second.1",
                tier=Tier.SYNTHESIS,
                source="m",
                checksum="b",
            ),
        )
        raise RuntimeError("install failed after linking")

    assert cfg.manifest_path.read_bytes() == before
