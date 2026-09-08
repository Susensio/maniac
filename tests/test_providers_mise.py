"""Tests for `MiseProvider` (ADR-0015 Stage 2): detection and resolution."""

from pathlib import Path

from maniac.models import RepoSource
from maniac.sources.providers import mise


def _make_mise_install(tmp_path: Path, tool: str, version: str, real_name: str) -> Path:
    """Build `<tmp>/.local/share/mise/installs/<tool>/<version>/.../<real_name>`."""
    root = tmp_path / ".local" / "share" / "mise" / "installs" / tool / version
    real = root / "extracted-dir" / real_name
    real.parent.mkdir(parents=True)
    real.touch()
    bin_path = tmp_path / ".local" / "bin" / real_name
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.symlink_to(real)
    return bin_path


def test_detect_fills_every_field_from_the_install_path(tmp_path: Path) -> None:
    bin_path = _make_mise_install(tmp_path, "helix", "25.07.1", "hx")
    provider = mise.MiseProvider()

    inst = provider.detect(bin_path)

    assert inst is not None
    assert inst.binary == "hx"
    assert inst.bin_path == bin_path
    assert inst.real_path == bin_path.resolve()
    assert inst.provider == "mise"
    assert inst.package == "helix"
    assert inst.version == "25.07.1"
    assert (
        inst.root
        == tmp_path / ".local" / "share" / "mise" / "installs" / "helix" / "25.07.1"
    )
    assert inst.parent is None  # composing mise's backend is Stage 3's job


def test_detect_keeps_the_raw_prefixed_tool_id(tmp_path: Path) -> None:
    """`npm-`/`pipx-`/... prefix parsing stays in `_resolve_from_mise` this stage."""
    bin_path = _make_mise_install(tmp_path, "npm-fish-lsp", "1.1.3", "fish-lsp")
    provider = mise.MiseProvider()

    inst = provider.detect(bin_path)

    assert inst is not None
    assert inst.package == "npm-fish-lsp"


def test_detect_rejects_a_path_outside_mise_installs(tmp_path: Path) -> None:
    real = tmp_path / "opt" / "somewhere" / "tool"
    real.parent.mkdir(parents=True)
    real.touch()
    bin_path = tmp_path / "bin" / "tool"
    bin_path.parent.mkdir()
    bin_path.symlink_to(real)

    assert mise.MiseProvider().detect(bin_path) is None


def test_detect_rejects_an_install_with_no_version_segment(tmp_path: Path) -> None:
    real = tmp_path / ".local" / "share" / "mise" / "installs" / "bare-tool"
    real.mkdir(parents=True)
    bin_path = real / "bare-tool"
    bin_path.touch()

    assert mise.MiseProvider().detect(bin_path) is None


def test_resolve_source_wraps_resolve_from_mise(tmp_path: Path, monkeypatch) -> None:
    bin_path = _make_mise_install(tmp_path, "helix", "25.07.1", "hx")
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None
    observed: dict[str, object] = {}

    def fake_resolve(tool_id: str, binary_name: str) -> str:
        observed["tool_id"] = tool_id
        observed["binary_name"] = binary_name
        return "helix-editor/helix"

    monkeypatch.setattr(mise.discovery, "_resolve_from_mise", fake_resolve)

    source = provider.resolve_source(inst)

    assert source == RepoSource(name="hx", target="helix-editor/helix", is_local=False)
    assert observed == {"tool_id": "helix", "binary_name": "hx"}


def test_resolve_source_returns_none_when_mise_registry_has_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    bin_path = _make_mise_install(tmp_path, "unknown-tool", "1.0", "unknown-tool")
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None

    monkeypatch.setattr(mise.discovery, "_resolve_from_mise", lambda *a, **k: None)

    assert provider.resolve_source(inst) is None


def test_local_docs_is_not_yet_wired(tmp_path: Path) -> None:
    """Stage 5 wires `local_docs` to the install root; Stage 2 stubs it honestly."""
    bin_path = _make_mise_install(tmp_path, "helix", "25.07.1", "hx")
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None

    assert provider.local_docs(inst) == []
