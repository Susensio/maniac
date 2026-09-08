"""Tests for `NpmProvider` (ADR-0015 Stage 4): detection and resolution."""

import json
from pathlib import Path

from maniac.models import RepoSource
from maniac.sources.providers import npm


def _make_npm_global(
    tmp_path: Path, package: str, real_name: str, package_json: dict[str, object]
) -> tuple[Path, Path]:
    """Build `<tmp>/lib/node_modules/<package>/`, return (bin_path, root)."""
    root = tmp_path / "lib" / "node_modules" / package
    root.mkdir(parents=True)
    (root / "package.json").write_text(json.dumps(package_json), encoding="utf-8")
    real = root / "bin" / real_name
    real.parent.mkdir(parents=True)
    real.touch()
    bin_path = tmp_path / "bin" / real_name
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.symlink_to(real)
    return bin_path, root


def test_detect_fills_every_field_from_package_json(tmp_path: Path) -> None:
    bin_path, root = _make_npm_global(
        tmp_path, "fish-lsp", "fish-lsp", {"name": "fish-lsp", "version": "1.1.3"}
    )
    provider = npm.NpmProvider()

    inst = provider.detect(bin_path)

    assert inst is not None
    assert inst.binary == "fish-lsp"
    assert inst.bin_path == bin_path
    assert inst.real_path == bin_path.resolve()
    assert inst.provider == "npm"
    assert inst.package == "fish-lsp"
    assert inst.version == "1.1.3"
    assert inst.root == root
    assert inst.parent is None


def test_detect_handles_a_scoped_package(tmp_path: Path) -> None:
    bin_path, root = _make_npm_global(
        tmp_path, "@scope/tool", "tool", {"name": "@scope/tool", "version": "2.0.0"}
    )
    inst = npm.NpmProvider().detect(bin_path)

    assert inst is not None
    assert inst.package == "@scope/tool"
    assert inst.root == root


def test_detect_rejects_a_path_outside_node_modules(tmp_path: Path) -> None:
    real = tmp_path / "opt" / "somewhere" / "tool"
    real.parent.mkdir(parents=True)
    real.touch()
    bin_path = tmp_path / "bin" / "tool"
    bin_path.parent.mkdir()
    bin_path.symlink_to(real)

    assert npm.NpmProvider().detect(bin_path) is None


def test_resolve_source_reads_the_string_repository_field(tmp_path: Path) -> None:
    bin_path, _root = _make_npm_global(
        tmp_path,
        "yaml-language-server",
        "yaml-language-server",
        {
            "name": "yaml-language-server",
            "version": "1.24.0",
            "repository": {
                "type": "git",
                "url": "git+https://github.com/redhat-developer/yaml-language-server.git",
            },
        },
    )
    inst = npm.NpmProvider().detect(bin_path)
    assert inst is not None

    source = npm.NpmProvider().resolve_source(inst)

    assert source == RepoSource(
        name="yaml-language-server",
        target="redhat-developer/yaml-language-server",
        is_local=False,
    )


def test_resolve_source_reads_the_github_shorthand(tmp_path: Path) -> None:
    bin_path, _root = _make_npm_global(
        tmp_path, "tool", "tool", {"name": "tool", "repository": "github:owner/tool"}
    )
    inst = npm.NpmProvider().detect(bin_path)
    assert inst is not None

    source = npm.NpmProvider().resolve_source(inst)

    assert source == RepoSource(name="tool", target="owner/tool", is_local=False)


def test_resolve_source_accepts_a_slash_terminated_repository_url(
    tmp_path: Path,
) -> None:
    bin_path, _root = _make_npm_global(
        tmp_path,
        "tool",
        "tool",
        {"name": "tool", "repository": "https://github.com/owner/tool/"},
    )
    inst = npm.NpmProvider().detect(bin_path)
    assert inst is not None

    source = npm.NpmProvider().resolve_source(inst)

    assert source == RepoSource(name="tool", target="owner/tool", is_local=False)


def test_resolve_source_returns_none_with_no_repository_field(tmp_path: Path) -> None:
    bin_path, _root = _make_npm_global(
        tmp_path, "tool", "tool", {"name": "tool", "version": "1.0.0"}
    )
    inst = npm.NpmProvider().detect(bin_path)
    assert inst is not None

    assert npm.NpmProvider().resolve_source(inst) is None


def test_local_docs_is_not_yet_wired(tmp_path: Path) -> None:
    bin_path, _root = _make_npm_global(tmp_path, "tool", "tool", {"name": "tool"})
    inst = npm.NpmProvider().detect(bin_path)
    assert inst is not None

    assert npm.NpmProvider().local_docs(inst) == []
