"""Tests for `CargoProvider` (ADR-0015 Stage 4): detection and resolution.

Modelled on the real `$CARGO_HOME/bin` on the development system: one
`cargo install`ed binary (`hexyl`) alongside nine rustup-managed shims
(`cargo`, `rustc`, `rust-analyzer`, ...), none a `cargo install` and none
listed in `.crates2.json`.
"""

import json
from pathlib import Path

from maniac.sources.providers import cargo


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


def test_resolve_source_returns_none(tmp_path, monkeypatch) -> None:
    """`.crates2.json` records no upstream repository; nothing here guesses one."""
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

    assert provider.resolve_source(inst) is None


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
