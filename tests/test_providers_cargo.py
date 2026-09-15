"""Tests for `CargoProvider` (ADR-0015 Stage 4): detection and resolution.

Modelled on the real `$CARGO_HOME/bin` on the development system: one
`cargo install`ed binary (`hexyl`) alongside nine rustup-managed shims
(`cargo`, `rustc`, `rust-analyzer`, ...), none a `cargo install` and none
listed in `.crates2.json`.
"""

import json
from pathlib import Path

import pytest

from maniac.config import Config
from maniac.sources.providers import cargo
from maniac.sources.providers.registry import registry


@pytest.fixture(autouse=True)
def _clear_cargo_metadata_cache() -> None:
    cargo._crates_by_binary.cache_clear()
    cargo._cargo_home.cache_clear()


def _make_cargo_home(tmp_path: Path, installs: dict[str, list[str]]) -> Path:
    """Build `<tmp>/cargo/bin/` with a `.crates2.json` recording `installs`.

    `installs` maps a `"<crate> <version> (<source>)"` key to its `bins`.
    """
    cargo_home = tmp_path / "cargo"
    (cargo_home / "bin").mkdir(parents=True)
    crates2 = {
        "installs": {key: {"bins": bins} for key, bins in installs.items()},
    }
    (cargo_home / ".crates2.json").write_text(json.dumps(crates2), encoding="utf-8")
    return cargo_home


def _provider(tmp_path: Path, monkeypatch, cargo_home: Path) -> cargo.CargoProvider:
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))
    return cargo.CargoProvider()


def test_detect_fills_every_field_for_a_real_crates2_entry(
    tmp_path, monkeypatch
) -> None:
    cargo_home = _make_cargo_home(
        tmp_path,
        {
            "hexyl 0.17.0 (registry+https://github.com/rust-lang/crates.io-index)": [
                "hexyl"
            ]
        },
    )
    bin_path = cargo_home / "bin" / "hexyl"
    bin_path.touch()
    provider = _provider(tmp_path, monkeypatch, cargo_home)

    inst = provider.detect(bin_path)

    assert inst is not None
    assert inst.binary == "hexyl"
    assert inst.bin_path == bin_path
    assert inst.real_path == bin_path.resolve()
    assert inst.provider == "cargo"
    assert inst.package == "hexyl"
    assert inst.version == "0.17.0"
    assert inst.root == (cargo_home / "bin").resolve()
    assert inst.parent is None


def test_detect_rejects_a_rustup_shim_not_in_crates2_json(
    tmp_path, monkeypatch
) -> None:
    """The trap: `rustc` is a real file in `$CARGO_HOME/bin` but never a `cargo
    install` and never listed in `.crates2.json` -- membership must gate, not
    mere presence in the directory.
    """
    cargo_home = _make_cargo_home(
        tmp_path,
        {
            "hexyl 0.17.0 (registry+https://github.com/rust-lang/crates.io-index)": [
                "hexyl"
            ]
        },
    )
    rustc = cargo_home / "bin" / "rustc"
    rustc.touch()
    provider = _provider(tmp_path, monkeypatch, cargo_home)

    assert provider.detect(rustc) is None


def test_detect_parses_each_cargo_metadata_file_once(tmp_path, monkeypatch) -> None:
    cargo_home = _make_cargo_home(
        tmp_path,
        {
            "hexyl 0.17.0 (registry+https://github.com/rust-lang/crates.io-index)": [
                "hexyl",
                "hexedit",
            ]
        },
    )
    hexyl = cargo_home / "bin" / "hexyl"
    hexedit = cargo_home / "bin" / "hexedit"
    hexyl.touch()
    hexedit.touch()
    provider = _provider(tmp_path, monkeypatch, cargo_home)
    loads = cargo.json.loads
    calls = 0

    def count_loads(*args, **kwargs):
        nonlocal calls
        calls += 1
        return loads(*args, **kwargs)

    monkeypatch.setattr(cargo.json, "loads", count_loads)

    assert provider.detect(hexyl) is not None
    assert provider.detect(hexedit) is not None
    assert calls == 1


def test_cargo_home_is_cached_and_isolated_by_environment_input(tmp_path) -> None:
    first = tmp_path / "first-cargo"
    second = tmp_path / "second-cargo"
    other_cwd = tmp_path / "other-cwd"

    assert cargo._cargo_home(str(first), str(tmp_path)) == first.resolve()
    assert cargo._cargo_home(str(first), str(tmp_path)) == first.resolve()
    assert cargo._cargo_home.cache_info().hits == 1
    assert cargo._cargo_home(str(second), str(tmp_path)) == second.resolve()
    assert cargo._cargo_home("relative-cargo", str(tmp_path)) == (
        tmp_path / "relative-cargo"
    )
    assert cargo._cargo_home("relative-cargo", str(other_cwd)) == (
        other_cwd / "relative-cargo"
    )
    assert cargo._cargo_home.cache_info().misses == 4


def test_detect_returns_none_without_cargo_home_set(tmp_path, monkeypatch) -> None:
    """Never assume `~/.cargo` -- with no `$CARGO_HOME`, nothing is claimed."""
    monkeypatch.delenv("CARGO_HOME", raising=False)
    bin_path = tmp_path / "cargo" / "bin" / "hexyl"
    bin_path.parent.mkdir(parents=True)
    bin_path.touch()

    assert cargo.CargoProvider().detect(bin_path) is None


def test_detect_rejects_a_binary_outside_cargo_bin(tmp_path, monkeypatch) -> None:
    cargo_home = _make_cargo_home(tmp_path, {})
    bin_path = tmp_path / "elsewhere" / "hexyl"
    bin_path.parent.mkdir(parents=True)
    bin_path.touch()
    provider = _provider(tmp_path, monkeypatch, cargo_home)

    assert provider.detect(bin_path) is None


_HEXYL_INSTALL = {
    "hexyl 0.17.0 (registry+https://github.com/rust-lang/crates.io-index)": ["hexyl"]
}


def _write_crate_source(
    cargo_home: Path,
    manifest: str,
    *,
    crate: str = "hexyl",
    version: str = "0.17.0",
    index: str = "index.crates.io-1949cf8c6b5b557f",
) -> Path:
    """Unpack a crate manifest where `cargo install` leaves it, under a
    per-machine index hash directory.
    """
    src = cargo_home / "registry" / "src" / index / f"{crate}-{version}"
    src.mkdir(parents=True)
    path = src / "Cargo.toml"
    path.write_text(manifest, encoding="utf-8")
    return path


def _hexyl_source(tmp_path, monkeypatch, manifest: str | None, **kwargs):
    """`(provider, installation)` for `hexyl`, with `manifest` unpacked unless None."""
    cargo_home = _make_cargo_home(tmp_path, _HEXYL_INSTALL)
    bin_path = cargo_home / "bin" / "hexyl"
    bin_path.touch()
    if manifest is not None:
        _write_crate_source(cargo_home, manifest, **kwargs)
    provider = _provider(tmp_path, monkeypatch, cargo_home)
    inst = provider.detect(bin_path)
    assert inst is not None
    return provider, inst


def test_resolve_source_reads_the_repository_the_crate_declares(
    tmp_path, monkeypatch
) -> None:
    """The real case: `hexyl 0.17.0`'s own manifest names `sharkdp/hexyl`."""
    provider, inst = _hexyl_source(
        tmp_path,
        monkeypatch,
        '[package]\nname = "hexyl"\nrepository = "https://github.com/sharkdp/hexyl"\n',
    )

    source = provider.resolve_source(inst, config=Config(), sources=registry)

    assert source is not None
    assert source.name == "hexyl"
    assert source.target == "sharkdp/hexyl"
    assert not source.is_local


def test_resolve_source_normalizes_a_git_suffixed_repository_url(
    tmp_path, monkeypatch
) -> None:
    """Crates on the development system ship `.git` suffixes (`anstyle`)."""
    provider, inst = _hexyl_source(
        tmp_path,
        monkeypatch,
        '[package]\nrepository = "https://github.com/sharkdp/hexyl.git"\n',
    )

    source = provider.resolve_source(inst, config=Config(), sources=registry)

    assert source is not None
    assert source.target == "sharkdp/hexyl"


def test_resolve_source_finds_the_crate_under_any_index_hash(
    tmp_path, monkeypatch
) -> None:
    """The index directory name varies per machine and per registry."""
    provider, inst = _hexyl_source(
        tmp_path,
        monkeypatch,
        '[package]\nrepository = "https://github.com/sharkdp/hexyl"\n',
        index="some-other-registry-0000000000000000",
    )

    source = provider.resolve_source(inst, config=Config(), sources=registry)

    assert source is not None
    assert source.target == "sharkdp/hexyl"


def test_resolve_source_returns_none_without_registry_source(
    tmp_path, monkeypatch
) -> None:
    """`cargo install --git`, a vendored install and a pruned registry cache
    all leave no unpacked source -- never a repository guessed from the name.
    """
    provider, inst = _hexyl_source(tmp_path, monkeypatch, None)

    assert provider.resolve_source(inst, config=Config(), sources=registry) is None


def test_resolve_source_returns_none_when_the_manifest_declares_no_repository(
    tmp_path, monkeypatch
) -> None:
    provider, inst = _hexyl_source(
        tmp_path,
        monkeypatch,
        '[package]\nname = "hexyl"\nhomepage = "https://example.com"\n',
    )

    assert provider.resolve_source(inst, config=Config(), sources=registry) is None


def test_resolve_source_returns_none_for_a_malformed_manifest(
    tmp_path, monkeypatch
) -> None:
    provider, inst = _hexyl_source(tmp_path, monkeypatch, "[package\nname = ")

    assert provider.resolve_source(inst, config=Config(), sources=registry) is None


def test_local_docs_finds_manpage_under_install_root(tmp_path, monkeypatch) -> None:
    cargo_home = _make_cargo_home(
        tmp_path,
        {
            "hexyl 0.17.0 (registry+https://github.com/rust-lang/crates.io-index)": [
                "hexyl"
            ]
        },
    )
    bin_path = cargo_home / "bin" / "hexyl"
    bin_path.touch()
    provider = _provider(tmp_path, monkeypatch, cargo_home)
    inst = provider.detect(bin_path)
    assert inst is not None
    manpage = cargo_home / "bin" / "hexyl.1"
    manpage.touch()

    assert provider.local_docs(inst) == [manpage]


def test_local_docs_finds_nothing_when_install_root_ships_no_manpage(
    tmp_path, monkeypatch
) -> None:
    cargo_home = _make_cargo_home(
        tmp_path,
        {
            "hexyl 0.17.0 (registry+https://github.com/rust-lang/crates.io-index)": [
                "hexyl"
            ]
        },
    )
    bin_path = cargo_home / "bin" / "hexyl"
    bin_path.touch()
    provider = _provider(tmp_path, monkeypatch, cargo_home)
    inst = provider.detect(bin_path)
    assert inst is not None

    assert provider.local_docs(inst) == []
