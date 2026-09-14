"""Tests for `MiseProvider` (ADR-0015 Stage 2): detection and resolution."""

from dataclasses import replace
from pathlib import Path

import pytest

from maniac.config import Config
from maniac.models import RepoSource
from maniac.sources.providers import mise
from maniac.sources.providers.base import DirectPageProvider
from maniac.sources.providers.npm import NpmProvider
from maniac.sources.providers.registry import registry


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


def test_mise_declares_the_direct_page_capability_others_do_not() -> None:
    """The capability is a type, not a duck-typed attribute lookup at the caller."""
    assert isinstance(mise.MiseProvider(), DirectPageProvider)
    assert not isinstance(NpmProvider(), DirectPageProvider)


def test_direct_page_target_uses_a_validated_alias(tmp_path: Path) -> None:
    bin_path = _make_mise_install(tmp_path, "tool", "1.0.0", "tool")
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None
    page = inst.root / "share" / "man" / "man1" / "tool.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    latest = inst.root.parent / "latest"
    latest.symlink_to(inst.root.name)

    assert provider.direct_page_target(inst, page) == latest / "share/man/man1/tool.1"


@pytest.mark.parametrize("alias_target", [None, "other"])
def test_direct_page_target_falls_back_without_a_matching_alias(
    tmp_path: Path, alias_target: str | None
) -> None:
    bin_path = _make_mise_install(tmp_path, "tool", "1.0.0", "tool")
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None
    page = inst.root / "share" / "man" / "man1" / "tool.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    if alias_target is not None:
        (inst.root.parent / alias_target).mkdir()
        (inst.root.parent / "latest").symlink_to(alias_target)

    assert provider.direct_page_target(inst, page) is None


def test_direct_page_target_rejects_a_binary_outside_the_alias_root(
    tmp_path: Path,
) -> None:
    bin_path = _make_mise_install(tmp_path, "tool", "1.0.0", "tool")
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None
    page = inst.root / "share" / "man" / "man1" / "tool.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    (inst.root.parent / "latest").symlink_to(inst.root.name)
    outside_binary = tmp_path / "outside" / "tool"
    outside_binary.parent.mkdir()
    outside_binary.touch()
    inst = replace(inst, real_path=outside_binary)

    assert provider.direct_page_target(inst, page) is None


def test_resolve_source_wraps_resolve_from_mise(tmp_path: Path, monkeypatch) -> None:
    bin_path = _make_mise_install(tmp_path, "helix", "25.07.1", "hx")
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None
    observed: dict[str, object] = {}

    def fake_resolve(
        tool_id: str, binary_name: str, *, config: Config, offline: bool = False
    ) -> str:
        observed["tool_id"] = tool_id
        observed["binary_name"] = binary_name
        observed["config"] = config
        observed["offline"] = offline
        return "helix-editor/helix"

    monkeypatch.setattr(mise.discovery, "_resolve_from_mise", fake_resolve)

    config = Config()
    source = provider.resolve_source(inst, config=config, sources=registry)

    assert source == RepoSource(name="hx", target="helix-editor/helix", is_local=False)
    assert observed == {
        "tool_id": "helix",
        "binary_name": "hx",
        "config": config,
        "offline": False,
    }


def test_resolve_source_returns_none_when_mise_registry_has_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    bin_path = _make_mise_install(tmp_path, "unknown-tool", "1.0", "unknown-tool")
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None

    monkeypatch.setattr(mise.discovery, "_resolve_from_mise", lambda *a, **k: None)

    assert provider.resolve_source(inst, config=Config(), sources=registry) is None


def test_resolve_source_uses_one_npm_package_when_no_backend_or_registry_source(
    tmp_path: Path, monkeypatch
) -> None:
    """Legacy installs use package layout, not their install directory name."""
    bin_path = _make_mise_install(
        tmp_path, "custom-language-server", "5.6.0", "bash-language-server"
    )
    root = bin_path.resolve().parents[1]
    package_dir = root / "lib" / "node_modules" / "bash-language-server"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        '{"name": "bash-language-server", "version": "5.6.0", '
        '"repository": {"type": "git", '
        '"url": "https://github.com/bash-lsp/bash-language-server"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(mise.discovery, "_resolve_from_mise", lambda *a, **k: None)
    inst = mise.MiseProvider().detect(bin_path)
    assert inst is not None

    source = mise.MiseProvider().resolve_source(inst, config=Config(), sources=registry)

    assert source == RepoSource(
        name="bash-language-server",
        target="bash-lsp/bash-language-server",
        is_local=False,
    )


def test_resolve_source_supports_root_node_modules_npm_layout(
    tmp_path: Path, monkeypatch
) -> None:
    bin_path = _make_mise_install(tmp_path, "custom-tool", "1.0", "custom-tool")
    root = bin_path.resolve().parents[1]
    package_dir = root / "node_modules" / "tool-package"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        '{"name": "tool-package", "repository": "github:owner/tool-package"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(mise.discovery, "_resolve_from_mise", lambda *a, **k: None)
    inst = mise.MiseProvider().detect(bin_path)
    assert inst is not None

    source = mise.MiseProvider().resolve_source(inst, config=Config(), sources=registry)

    assert source == RepoSource(
        name="custom-tool", target="owner/tool-package", is_local=False
    )


def test_resolve_source_prefers_npm_package_metadata_to_mise_inference(
    tmp_path: Path, monkeypatch
) -> None:
    bin_path = _make_mise_install(tmp_path, "custom-tool", "1.0", "custom-tool")
    root = bin_path.resolve().parents[1]
    package_dir = root / "node_modules" / "tool-package"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        '{"name": "tool-package", "repository": "github:package/repository"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        mise.discovery, "_resolve_from_mise", lambda *a, **k: "inferred/repository"
    )
    inst = mise.MiseProvider().detect(bin_path)
    assert inst is not None

    source = mise.MiseProvider().resolve_source(inst, config=Config(), sources=registry)

    assert source == RepoSource(
        name="custom-tool", target="package/repository", is_local=False
    )


def test_resolve_source_refuses_ambiguous_npm_package_layout(
    tmp_path: Path, monkeypatch
) -> None:
    bin_path = _make_mise_install(tmp_path, "custom-tool", "1.0", "custom-tool")
    root = bin_path.resolve().parents[1]
    for package in ("first", "second"):
        package_dir = root / "node_modules" / package
        package_dir.mkdir(parents=True)
        (package_dir / "package.json").write_text(
            f'{{"name": "{package}", "repository": "github:owner/{package}"}}',
            encoding="utf-8",
        )
    monkeypatch.setattr(mise.discovery, "_resolve_from_mise", lambda *a, **k: None)
    inst = mise.MiseProvider().detect(bin_path)
    assert inst is not None

    assert (
        mise.MiseProvider().resolve_source(inst, config=Config(), sources=registry)
        is None
    )


def test_resolve_source_prefers_backend_record_to_npm_layout(
    tmp_path: Path, monkeypatch
) -> None:
    bin_path = _make_mise_install(tmp_path, "custom-tool", "1.0", "custom-tool")
    root = bin_path.resolve().parents[1]
    (root.parent / ".mise.backend.toml").write_text(
        'full = "aqua:record/repository"\n', encoding="utf-8"
    )
    package_dir = root / "node_modules" / "other-package"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        '{"name": "other-package", "repository": "github:layout/repository"}',
        encoding="utf-8",
    )

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("registry fallback must not run when the record exists")

    monkeypatch.setattr(mise.discovery, "_resolve_from_mise", fail_if_called)
    inst = mise.MiseProvider().detect(bin_path)
    assert inst is not None

    source = mise.MiseProvider().resolve_source(inst, config=Config(), sources=registry)

    assert source == RepoSource(
        name="custom-tool", target="record/repository", is_local=False
    )


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

    source = provider.resolve_source(inst, config=Config(), sources=registry)

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

    source = provider.resolve_source(inst, config=Config(), sources=registry)

    assert source == RepoSource(
        name="herdr", target="ogulcancelik/herdr", is_local=False
    )


def test_resolve_source_keeps_tmux_build_repository_as_distribution_provenance(
    tmp_path: Path,
) -> None:
    bin_path = _make_mise_install(tmp_path, "tmux", "3.7b", "tmux")
    (bin_path.resolve().parents[2] / ".mise.backend.toml").write_text(
        'full = "aqua:tmux/tmux-builds"\n', encoding="utf-8"
    )
    inst = mise.MiseProvider().detect(bin_path)
    assert inst is not None

    source = mise.MiseProvider().resolve_source(inst, config=Config(), sources=registry)

    assert source == RepoSource(
        name="tmux",
        target="tmux/tmux-builds",
        is_local=False,
    )


def test_resolve_source_does_not_redirect_an_unrelated_builds_repository(
    tmp_path: Path,
) -> None:
    bin_path = _make_mise_install(tmp_path, "foo", "1.0", "foo")
    (bin_path.resolve().parents[2] / ".mise.backend.toml").write_text(
        'full = "aqua:owner/foo-builds"\n', encoding="utf-8"
    )
    inst = mise.MiseProvider().detect(bin_path)
    assert inst is not None

    source = mise.MiseProvider().resolve_source(inst, config=Config(), sources=registry)

    assert source == RepoSource(name="foo", target="owner/foo-builds", is_local=False)


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

    assert provider.resolve_source(inst, config=Config(), sources=registry) is None


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

    source = provider.resolve_source(inst, config=Config(), sources=registry)

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
    monkeypatch.setattr(
        mise.discovery, "_load_mise_registry", lambda cache_path: {"nufmt": "x/y"}
    )

    # The registry only has an entry under the bare binary name, not the
    # installation-derived directory name -- restrictive resolution must miss.
    assert (
        provider.resolve_source(
            inst,
            config=Config(config_dir=tmp_path / "empty-config"),
            sources=registry,
        )
        is None
    )


def test_resolve_source_offline_skips_the_registry_but_keeps_local_config(
    tmp_path: Path, monkeypatch
) -> None:
    """`offline=True` (ADR-0018) must never reach `_query_mise_registry` --
    proven here by making it raise -- while a local `tool_alias`/`tools`
    match still resolves.
    """
    bin_path = _make_mise_install(tmp_path, "ripgrep", "14.1.0", "rg")
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None
    mise_dir = tmp_path / "config" / "mise"
    mise_dir.mkdir(parents=True)
    (mise_dir / "config.toml").write_text(
        "[tool_alias]\nripgrep = 'github:private/rg'\n", encoding="utf-8"
    )

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("registry fallback must not run when offline")

    monkeypatch.setattr(mise.discovery, "_query_mise_registry", fail_if_called)

    source = provider.resolve_source(
        inst,
        config=Config(config_dir=tmp_path / "config"),
        sources=registry,
        offline=True,
    )

    assert source == RepoSource(name="rg", target="private/rg", is_local=False)


def test_resolve_source_offline_returns_none_rather_than_query_the_registry(
    tmp_path: Path, monkeypatch
) -> None:
    """No local config match, `offline=True`: unresolved, not a registry query."""
    bin_path = _make_mise_install(
        tmp_path, "cargo-https-github-com-nushell-nufmt", "HEAD", "nufmt"
    )
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty-config"))

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("registry fallback must not run when offline")

    monkeypatch.setattr(mise.discovery, "_query_mise_registry", fail_if_called)

    assert (
        provider.resolve_source(inst, config=Config(), sources=registry, offline=True)
        is None
    )


def test_local_docs_finds_manpage_under_install_root(tmp_path: Path) -> None:
    """Stage 5: `local_docs` reads the install root, no manpath lookup involved."""
    bin_path = _make_mise_install(tmp_path, "helix", "25.07.1", "hx")
    provider = mise.MiseProvider()
    inst = provider.detect(bin_path)
    assert inst is not None
    manpage = inst.root / "share" / "man" / "man1" / "hx.1.gz"
    manpage.parent.mkdir(parents=True)
    manpage.touch()

    assert provider.local_docs(inst) == [manpage]


def test_local_docs_finds_nothing_when_install_root_ships_no_manpage(
    tmp_path: Path,
) -> None:
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

    source = provider.resolve_source(inst, config=Config(), sources=registry)

    assert source == RepoSource(
        name="yaml-language-server",
        target="redhat-developer/yaml-language-server",
        is_local=False,
    )


def test_resolve_source_delegates_a_parent_to_the_resolver_it_was_given(
    tmp_path: Path,
) -> None:
    """The composed install is resolved through the caller's resolver, so nothing
    binds a resolver onto `MiseProvider` itself (ADR-0015 composition).
    """
    bin_path = _make_mise_install(
        tmp_path, "npm-yaml-language-server", "1.24.0", "yaml-language-server"
    )
    root = bin_path.resolve().parents[1]
    (root.parent / ".mise.backend.toml").write_text(
        'full = "npm:yaml-language-server"\n', encoding="utf-8"
    )
    inst = mise.MiseProvider().detect(bin_path)
    assert inst is not None and inst.parent is not None
    config = Config()
    observed: list[tuple[object, Config]] = []

    class RecordingResolver:
        def resolve_source(self, inst, *, config: Config) -> RepoSource:
            observed.append((inst, config))
            return RepoSource(
                name="yaml-language-server",
                target="owner/repo",
                is_local=False,
            )

    source = mise.MiseProvider().resolve_source(
        inst, config=config, sources=RecordingResolver()
    )

    assert source == RepoSource(
        name="yaml-language-server", target="owner/repo", is_local=False
    )
    assert observed == [(inst.parent, config)]


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

    source = provider.resolve_source(inst, config=Config(), sources=registry)

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
    assert provider.resolve_source(inst, config=Config(), sources=registry) is None
