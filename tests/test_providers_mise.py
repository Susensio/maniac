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
    """Prefix parsing is gone from `_resolve_from_mise`; `detect` never did any."""
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


def test_resolve_source_reads_the_backend_record_before_the_registry(
    tmp_path: Path, monkeypatch
) -> None:
    """`.mise.backend.toml`'s `full` field gives backend and package identity
    directly (ADR-0015 Stage 3); the registry is never consulted when it's there.
    """
    bin_path = _make_mise_install(tmp_path, "biome", "2.5.9", "biome")
    (bin_path.resolve().parents[2] / ".mise.backend.toml").write_text(
        'short = "biome"\nfull = "aqua:biomejs/biome"\nexplicit_backend = false\n',
        encoding="utf-8",
    )
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("registry fallback must not run when the record exists")

    monkeypatch.setattr(mise.discovery, "_resolve_from_mise", fail_if_called)

    source = provider.resolve_source(inst)

    assert source == RepoSource(name="biome", target="biomejs/biome", is_local=False)


def test_resolve_source_treats_github_backend_the_same_as_aqua(
    tmp_path: Path,
) -> None:
    bin_path = _make_mise_install(tmp_path, "herdr", "1.0.0", "herdr")
    (bin_path.resolve().parents[2] / ".mise.backend.toml").write_text(
        'full = "github:ogulcancelik/herdr"\n', encoding="utf-8"
    )
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None

    source = provider.resolve_source(inst)

    assert source == RepoSource(
        name="herdr", target="ogulcancelik/herdr", is_local=False
    )


def test_resolve_source_gives_up_when_the_composed_npm_package_json_is_missing(
    tmp_path: Path,
) -> None:
    """The npm backend composes a parent installation (Stage 4), but delegation
    still yields nothing when the composed location has no `package.json` to
    read -- `NpmProvider` returns `None` rather than guessing.
    """
    bin_path = _make_mise_install(
        tmp_path, "npm-yaml-language-server", "1.24.0", "yaml-language-server"
    )
    (bin_path.resolve().parents[2] / ".mise.backend.toml").write_text(
        'full = "npm:yaml-language-server"\n', encoding="utf-8"
    )
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None

    assert provider.resolve_source(inst) is None


def test_resolve_source_falls_back_when_the_backend_record_is_not_utf8(
    tmp_path: Path, monkeypatch
) -> None:
    """A corrupt or oddly-encoded `.mise.backend.toml` must not crash discovery."""
    bin_path = _make_mise_install(tmp_path, "helix", "25.07.1", "hx")
    (bin_path.resolve().parents[2] / ".mise.backend.toml").write_bytes(b"\xff\xfe\x00")
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None
    monkeypatch.setattr(
        mise.discovery, "_resolve_from_mise", lambda *a, **k: "helix-editor/helix"
    )

    source = provider.resolve_source(inst)

    assert source == RepoSource(name="hx", target="helix-editor/helix", is_local=False)


def test_resolve_source_falls_back_to_the_registry_keyed_on_the_directory_name(
    tmp_path: Path, monkeypatch
) -> None:
    """No `.mise.backend.toml` (a legacy install, e.g. `cargo-https-...-nufmt`):
    the registry is queried with the install directory name, never the bare
    binary name -- `_resolve_from_mise`'s permissive fallback is gone (ADR-0015).
    """
    bin_path = _make_mise_install(
        tmp_path, "cargo-https-github-com-nushell-nufmt", "HEAD", "nufmt"
    )
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty-config"))
    monkeypatch.setattr(mise.discovery, "_load_mise_registry", lambda: {"nufmt": "x/y"})

    # The registry only has an entry under the bare binary name, not the
    # installation-derived directory name -- restrictive resolution must miss.
    assert provider.resolve_source(inst) is None


def test_local_docs_is_not_yet_wired(tmp_path: Path) -> None:
    """Stage 5 wires `local_docs` to the install root; Stage 2 stubs it honestly."""
    bin_path = _make_mise_install(tmp_path, "helix", "25.07.1", "hx")
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None

    assert provider.local_docs(inst) == []


def test_detect_composes_a_parent_for_an_npm_backend(tmp_path: Path) -> None:
    """Real shape from the development system: mise's npm backend places the
    package's own `package.json` under `node_modules/<pkg>/`, not directly at
    the install root -- that root holds mise's own wrapper package instead.
    """
    bin_path = _make_mise_install(
        tmp_path, "npm-yaml-language-server", "1.24.0", "yaml-language-server"
    )
    root = bin_path.resolve().parents[1]
    (root.parent / ".mise.backend.toml").write_text(
        'full = "npm:yaml-language-server"\n', encoding="utf-8"
    )
    package_dir = root / "node_modules" / "yaml-language-server"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        '{"name": "yaml-language-server", "version": "1.24.0", '
        '"repository": {"type": "git", '
        '"url": "git+https://github.com/redhat-developer/yaml-language-server.git"}}',
        encoding="utf-8",
    )
    provider = mise.MiseProvider()

    inst = provider.detect(bin_path)

    assert inst is not None
    assert inst.parent is not None
    assert inst.parent.provider == "npm"
    assert inst.parent.package == "yaml-language-server"
    assert inst.parent.root == package_dir

    source = provider.resolve_source(inst)

    assert source == RepoSource(
        name="yaml-language-server",
        target="redhat-developer/yaml-language-server",
        is_local=False,
    )


def test_detect_composes_a_parent_for_a_pipx_backend(tmp_path: Path) -> None:
    """Real shape from the development system: mise's pipx backend nests the
    venv one level below its own install root, named after the package.
    """
    bin_path = _make_mise_install(tmp_path, "pipx-tlp-ui", "1.10.1", "tlpui")
    root = bin_path.resolve().parents[1]
    (root.parent / ".mise.backend.toml").write_text(
        'full = "pipx:tlp-ui"\n', encoding="utf-8"
    )
    venv_root = root / "tlp-ui"
    dist_info = (
        venv_root / "lib" / "python3.12" / "site-packages" / "tlp_ui-1.10.1.dist-info"
    )
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.4\n"
        "Name: tlp-ui\n"
        "Version: 1.10.1\n"
        "Project-URL: Repository, https://github.com/d4nj1/TLPUI\n",
        encoding="utf-8",
    )
    provider = mise.MiseProvider()

    inst = provider.detect(bin_path)

    assert inst is not None
    assert inst.parent is not None
    assert inst.parent.provider == "pipx"
    assert inst.parent.package == "tlp-ui"
    assert inst.parent.root == venv_root

    source = provider.resolve_source(inst)

    assert source == RepoSource(name="tlpui", target="d4nj1/TLPUI", is_local=False)


def test_detect_leaves_parent_none_for_a_backend_with_no_composed_shape(
    tmp_path: Path,
) -> None:
    """`.mise.backend.toml` names a backend (mise's own "core") with neither a
    direct-repo shape nor a registered provider to delegate to -- no parent,
    and resolution stays unresolved rather than guessed.
    """
    bin_path = _make_mise_install(tmp_path, "python", "3.13.0", "python3")
    root = bin_path.resolve().parents[1]
    (root.parent / ".mise.backend.toml").write_text(
        'full = "core:python"\n', encoding="utf-8"
    )
    provider = mise.MiseProvider()

    inst = provider.detect(bin_path)

    assert inst is not None
    assert inst.parent is None
    assert provider.resolve_source(inst) is None
