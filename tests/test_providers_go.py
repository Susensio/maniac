"""Tests for `GoProvider` (ADR-0015 Stage 4): detection and resolution.

`go version -m` is mocked at the module boundary (`go._read_module_info`)
rather than shelled out to in tests -- mirrors `test_providers_mise.py`
monkeypatching `mise.discovery._resolve_from_mise`.
"""

import os
from pathlib import Path

from maniac.models import RepoSource
from maniac.sources.providers import go


def _make_go_bin(gobin: Path, real_name: str) -> Path:
    gobin.mkdir(parents=True, exist_ok=True)
    bin_path = gobin / real_name
    bin_path.touch()
    return bin_path


def test_detect_fills_every_field_from_module_info(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GOBIN", raising=False)
    monkeypatch.delenv("GOPATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    gobin = tmp_path / "go" / "bin"
    bin_path = _make_go_bin(gobin, "goimports")
    monkeypatch.setattr(
        go,
        "_read_module_info",
        lambda path: (
            "golang.org/x/tools/cmd/goimports",
            "golang.org/x/tools",
            "v0.49.0",
        ),
    )
    provider = go.GoProvider()

    inst = provider.detect(bin_path)

    assert inst is not None
    assert inst.binary == "goimports"
    assert inst.bin_path == bin_path
    assert inst.real_path == bin_path.resolve()
    assert inst.provider == "go"
    assert inst.package == "golang.org/x/tools/cmd/goimports"
    assert inst.version == "v0.49.0"
    assert inst.root == gobin
    assert inst.parent is None


def test_detect_honors_gobin_when_set(tmp_path, monkeypatch) -> None:
    gobin = tmp_path / "custom-gobin"
    monkeypatch.setenv("GOBIN", str(gobin))
    bin_path = _make_go_bin(gobin, "tool")
    monkeypatch.setattr(
        go,
        "_read_module_info",
        lambda path: ("example.com/tool", "example.com/tool", "v1.0.0"),
    )

    inst = go.GoProvider().detect(bin_path)

    assert inst is not None
    assert inst.root == gobin


def test_detect_uses_only_the_first_gopath_entry(tmp_path, monkeypatch) -> None:
    """`GOPATH` may list several `os.pathsep`-separated directories; `go
    install` only ever uses the first one's `bin`."""
    monkeypatch.delenv("GOBIN", raising=False)
    first = tmp_path / "work"
    second = tmp_path / "shared"
    monkeypatch.setenv("GOPATH", f"{first}{os.pathsep}{second}")
    bin_path = _make_go_bin(first / "bin", "tool")
    monkeypatch.setattr(
        go,
        "_read_module_info",
        lambda path: ("example.com/tool", "example.com/tool", "v1.0.0"),
    )

    inst = go.GoProvider().detect(bin_path)

    assert inst is not None
    assert inst.root == first / "bin"


def test_detect_rejects_a_binary_outside_gobin(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GOBIN", str(tmp_path / "go-bin"))
    (tmp_path / "go-bin").mkdir()
    bin_path = tmp_path / "elsewhere" / "tool"
    bin_path.parent.mkdir()
    bin_path.touch()

    assert go.GoProvider().detect(bin_path) is None


def test_detect_rejects_go_itself_when_mise_managed(tmp_path, monkeypatch) -> None:
    """The `go` toolchain binary lives elsewhere (mise-managed); GOBIN's own
    directory never contains it, so it is never mistakenly claimed.
    """
    monkeypatch.setenv("GOBIN", str(tmp_path / "go" / "bin"))
    mise_go = tmp_path / "mise" / "installs" / "go" / "1.27.1" / "bin" / "go"
    mise_go.parent.mkdir(parents=True)
    mise_go.touch()

    assert go.GoProvider().detect(mise_go) is None


def test_detect_returns_none_when_module_info_is_unreadable(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GOBIN", str(tmp_path / "go-bin"))
    bin_path = _make_go_bin(tmp_path / "go-bin", "tool")
    monkeypatch.setattr(go, "_read_module_info", lambda path: None)

    assert go.GoProvider().detect(bin_path) is None


def test_resolve_source_derives_owner_repo_from_a_github_module(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GOBIN", str(tmp_path / "go-bin"))
    bin_path = _make_go_bin(tmp_path / "go-bin", "herdr")
    monkeypatch.setattr(
        go,
        "_read_module_info",
        lambda path: (
            "github.com/ogulcancelik/herdr",
            "github.com/ogulcancelik/herdr",
            "v1.0.0",
        ),
    )
    inst = go.GoProvider().detect(bin_path)
    assert inst is not None

    source = go.GoProvider().resolve_source(inst)

    assert source == RepoSource(
        name="herdr", target="ogulcancelik/herdr", is_local=False
    )


def test_resolve_source_returns_none_for_a_non_github_module(
    tmp_path, monkeypatch
) -> None:
    """`golang.org/x/tools` is a vanity import path; mapping it to a GitHub
    mirror would need a network lookup this provider does not make.
    """
    monkeypatch.setenv("GOBIN", str(tmp_path / "go-bin"))
    bin_path = _make_go_bin(tmp_path / "go-bin", "goimports")
    monkeypatch.setattr(
        go,
        "_read_module_info",
        lambda path: (
            "golang.org/x/tools/cmd/goimports",
            "golang.org/x/tools",
            "v0.49.0",
        ),
    )
    inst = go.GoProvider().detect(bin_path)
    assert inst is not None

    assert go.GoProvider().resolve_source(inst) is None


def test_local_docs_is_not_yet_wired(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GOBIN", str(tmp_path / "go-bin"))
    bin_path = _make_go_bin(tmp_path / "go-bin", "tool")
    monkeypatch.setattr(
        go,
        "_read_module_info",
        lambda path: ("example.com/tool", "example.com/tool", "v1.0.0"),
    )
    inst = go.GoProvider().detect(bin_path)
    assert inst is not None

    assert go.GoProvider().local_docs(inst) == []
