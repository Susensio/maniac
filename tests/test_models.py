"""Tests for `RepoSource.clone_url`."""

from pathlib import Path

import pytest

from maniac.models import LocalRepoSource, RemoteRepoSource, RepoSource


def test_source_variants_carry_identity_and_clone_data() -> None:
    local = LocalRepoSource("tool", Path("/workspace/tool"))
    remote = RemoteRepoSource.from_identifier("tool", "owner/tool")

    assert local.identity == "LOCAL:/workspace/tool"
    assert local.clone_url is None
    assert remote.identity == "owner/tool"
    assert remote.clone_url == "https://github.com/owner/tool.git"


def test_direct_remote_variant_requires_its_derived_clone_data() -> None:
    assert (
        RemoteRepoSource(
            "tool", "aqua:sharkdp/pastel", "https://github.com/sharkdp/pastel.git"
        ).clone_url
        == "https://github.com/sharkdp/pastel.git"
    )
    assert RemoteRepoSource("tool", "npm:tool", None).clone_url is None

    with pytest.raises(ValueError, match="clone URL"):
        RemoteRepoSource("tool", "npm:tool", "https://github.com/wrong/tool.git")
    with pytest.raises(ValueError, match="reserved local"):
        RemoteRepoSource("tool", "LOCAL:/workspace/tool", None)


def test_legacy_constructor_rejects_impossible_correlated_fields() -> None:
    with pytest.raises(ValueError, match="local source target"):
        RepoSource(
            name="tool",
            target="https://example.com/tool",
            is_local=True,
            local_path=Path("/workspace/tool"),
        )
    with pytest.raises(ValueError, match="remote source"):
        RepoSource(
            name="tool",
            target="LOCAL:/workspace/tool",
            is_local=False,
        )


def test_clone_url_bare_owner_repo() -> None:
    source = RepoSource(name="rg", target="BurntSushi/ripgrep", is_local=False)
    assert source.clone_url == "https://github.com/BurntSushi/ripgrep.git"


def test_clone_url_passes_through_explicit_url() -> None:
    source = RepoSource(name="rg", target="https://gitlab.com/foo/rg", is_local=False)
    assert source.clone_url == "https://gitlab.com/foo/rg"


def test_clone_url_strips_aqua_backend_prefix() -> None:
    """A Mise `tool_alias` naming an aqua backend leaks its prefix into `target`.

    Aqua's package identifier is itself an owner/repo pair, so it is the one
    backend prefix worth resolving to a real link.
    """
    source = RepoSource(name="pastel", target="aqua:sharkdp/pastel", is_local=False)
    assert source.clone_url == "https://github.com/sharkdp/pastel.git"


def test_clone_url_other_backend_prefixes_are_not_linked() -> None:
    """npm/pipx/cargo package names have no fixed relationship to a GitHub path.

    Guessing a link would point at the wrong repository, so `clone_url` is
    None here rather than a plausible-looking but wrong URL -- the caller
    (`_repo_cell`) already renders a None `clone_url` as plain text.
    """
    for target in ("npm:eslint", "pipx:howdoi", "cargo:ripgrep", "vfox:node"):
        source = RepoSource(name="tool", target=target, is_local=False)
        assert source.clone_url is None, target


def test_clone_url_local_source_is_never_linked() -> None:
    source = RepoSource(
        name="yadm",
        target="LOCAL:/home/user/.local/lib/yadm",
        is_local=True,
        local_path=Path("/home/user/.local/lib/yadm"),
    )
    assert source.clone_url is None


def test_clone_url_unresolved_binary_registry_match_is_not_linked() -> None:
    """`discover_repo`'s fallback sets target == name with no "/" at all."""
    source = RepoSource(name="rg", target="rg", is_local=False)
    assert source.clone_url is None
