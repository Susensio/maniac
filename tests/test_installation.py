"""Tests for `Installation` (ADR-0015): a frozen, sloted record of a detected binary."""

import dataclasses
from pathlib import Path

import pytest

from maniac.models import Installation

_BASE = Installation(
    binary="hx",
    bin_path=Path("/home/user/.local/bin/hx"),
    real_path=Path("/home/user/.local/share/mise/installs/helix/25.01/bin/hx"),
    provider="mise",
    package="helix",
    version="25.01",
    root=Path("/home/user/.local/share/mise/installs/helix/25.01"),
)


def test_field_order_matches_roadmap_positional_construction() -> None:
    """Roadmap's Stage 1 block fixes both the field set and their order."""
    inst = Installation(
        "hx",
        Path("/home/user/.local/bin/hx"),
        Path("/real/hx"),
        "mise",
        "helix",
        "25.01",
        Path("/root"),
    )
    assert inst.binary == "hx"
    assert inst.bin_path == Path("/home/user/.local/bin/hx")
    assert inst.real_path == Path("/real/hx")
    assert inst.provider == "mise"
    assert inst.package == "helix"
    assert inst.version == "25.01"
    assert inst.root == Path("/root")
    assert inst.parent is None


def test_parent_defaults_to_none() -> None:
    assert _BASE.parent is None


def test_parent_composes_a_backend_installation() -> None:
    """Mise composes rather than special-cases (ADR-0015): its `Installation` links out."""
    backend = dataclasses.replace(_BASE, binary="biome", provider="aqua")
    mise = dataclasses.replace(_BASE, provider="mise", parent=backend)
    assert mise.parent is backend
    assert mise.parent.provider == "aqua"


def test_version_is_optional() -> None:
    assert dataclasses.replace(_BASE, version=None).version is None


def test_is_frozen() -> None:
    field_name = (
        "version"  # not a literal attribute access, so `ty` cannot flag it statically
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(_BASE, field_name, "26.01")


def test_is_slotted() -> None:
    """`slots=True`: no `__dict__`, and the slots match the field set exactly."""
    assert not hasattr(_BASE, "__dict__")
    assert Installation.__slots__ == (
        "binary",
        "bin_path",
        "real_path",
        "provider",
        "package",
        "version",
        "root",
        "parent",
        "losers",
    )


def test_equality_is_by_value() -> None:
    assert dataclasses.replace(_BASE) == _BASE
    assert dataclasses.replace(_BASE, version="26.01") != _BASE
