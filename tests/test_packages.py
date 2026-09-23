"""Tests for external manpage package provenance."""

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from maniac.models import RemoteRepoSource
from maniac.sources import packages


@pytest.fixture(autouse=True)
def _clear_debian_caches() -> None:
    packages._debian_owner.cache_clear()
    packages._debian_version.cache_clear()
    packages._debian_source.cache_clear()
    packages._debian_homepage.cache_clear()


def _dpkg(
    *, owner: str, version: str = "", source: str = "", homepage: str = ""
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Fake `dpkg-query`, dispatching on which field the args request."""

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "-S" in args:
            return subprocess.CompletedProcess(
                args, 0, f"{owner}: /usr/share/man/man1/tldr.1.gz\n", ""
            )
        if any("${Source}" in arg for arg in args):
            return subprocess.CompletedProcess(args, 0, f"{source}\n", "")
        if any("${Homepage}" in arg for arg in args):
            return subprocess.CompletedProcess(args, 0, f"{homepage}\n", "")
        return subprocess.CompletedProcess(args, 0, f"{version}\n", "")

    return run


def test_verify_external_page_marks_tldr_shaped_debian_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        packages.subprocess,
        "run",
        _dpkg(owner="tealdeer:amd64", version="1.6.1-4build2"),
    )

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tldr.1.gz"), package="tealdeer", version="1.9.0"
    )

    assert result.freshness is packages.ExternalPageFreshness.MISMATCH


def test_verify_external_page_accepts_matching_debian_epoch_and_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        packages.subprocess,
        "run",
        _dpkg(owner="tealdeer:amd64", version="2:1.9.0-1ubuntu1"),
    )

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tldr.1.gz"), package="tealdeer", version="1.9.0"
    )

    assert result.freshness is packages.ExternalPageFreshness.MATCH


def test_verify_external_page_reaches_unverified_when_sameness_is_unproven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dpkg naming an owner that normalization cannot tie to the provider's
    own package is `UNVERIFIED`, not `WRONG_OWNER` (ADR-0055): dpkg found
    evidence that does not decide the case, not evidence of a wrong owner."""
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return _dpkg(owner="tmux:amd64", source="tmux")(args, **kwargs)

    monkeypatch.setattr(packages.subprocess, "run", run)

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tldr.1.gz"), package="tealdeer", version="1.9.0"
    )

    assert result.freshness is packages.ExternalPageFreshness.UNVERIFIED
    assert result.owner == "tmux:amd64"
    assert len(calls) == 2


def test_verify_external_page_stays_unverified_when_no_owner_is_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "no path found matching\n")

    monkeypatch.setattr(packages.subprocess, "run", run)

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tldr.1.gz"), package="tealdeer", version="1.9.0"
    )

    assert result.freshness is packages.ExternalPageFreshness.UNVERIFIED
    assert result.owner is None


def test_verify_external_page_degrades_when_dpkg_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(packages.subprocess, "run", missing)

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tldr.1.gz"), package="tealdeer", version="1.9.0"
    )

    assert result.freshness is packages.ExternalPageFreshness.UNVERIFIED
    assert result.owner is None


def test_verify_external_page_skips_the_owner_lookup_without_a_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return _dpkg(owner="tealdeer:amd64", version="1.9.0-1")(args, **kwargs)

    monkeypatch.setattr(packages.subprocess, "run", run)

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tldr.1.gz"), package="tealdeer", version=None
    )

    assert result.freshness is packages.ExternalPageFreshness.UNVERIFIED
    assert result.owner is None
    assert calls == []


def test_verify_external_page_caches_owner_and_version_subprocesses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return _dpkg(owner="tealdeer:amd64", version="1.9.0-1")(args, **kwargs)

    monkeypatch.setattr(packages.subprocess, "run", run)
    page = Path("/usr/share/man/man1/tldr.1.gz")

    assert (
        packages.verify_external_page(
            page, package="tealdeer", version="1.9.0"
        ).freshness
        is packages.ExternalPageFreshness.MATCH
    )
    assert (
        packages.verify_external_page(
            page, package="tealdeer", version="1.9.0"
        ).freshness
        is packages.ExternalPageFreshness.MATCH
    )
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("owner", "source", "package"),
    [
        # Real dpkg-query -W -f='${Source}' output on this machine.
        ("python3.12-minimal:amd64", "python3.12", "python"),
        ("libpython3.12-dev:amd64", "python3.12", "python"),
        ("gcc-13", "", "gcc"),
        # Hypothetical: Debian renaming the binary package itself to its
        # `${Source}` shape, exercising the language-team prefix strip
        # against a package name exact-match would no longer catch.
        ("rust-tealdeer", "", "tealdeer"),
    ],
)
def test_verify_external_page_proves_sameness_through_normalized_source(
    monkeypatch: pytest.MonkeyPatch, owner: str, source: str, package: str
) -> None:
    """Debian's mechanical naming (ADR-0055) proves the python rows that
    used to false-positive as `WRONG_OWNER` are the same software, reaching
    the version comparison instead."""
    monkeypatch.setattr(
        packages.subprocess,
        "run",
        _dpkg(owner=owner, version="1.9.0-1", source=source),
    )

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tool.1"), package=package, version="1.9.0"
    )

    assert result.freshness is packages.ExternalPageFreshness.MATCH


@pytest.mark.parametrize(
    ("name", "normalized"),
    [
        ("python3.12", "python"),
        ("gcc-13", "gcc"),
        ("rust-tealdeer", "tealdeer"),
        ("node-typescript", "typescript"),
        ("haskell-pandoc", "pandoc"),
        ("ruby-rack", "rack"),
        ("golang-github-sirupsen-logrus", "logrus"),
        ("python3.12-minimal", "python"),
    ],
)
def test_normalize_debian_name_applies_exactly_the_adr_0055_rewrites(
    name: str, normalized: str
) -> None:
    assert packages._normalize_debian_name(name) == normalized


def test_normalize_debian_name_leaves_unmatched_names_alone() -> None:
    assert packages._normalize_debian_name("ripgrep") == "ripgrep"


def test_normalize_debian_name_leaves_bzip2_alone() -> None:
    """`2` is part of bzip2's own name, not a Debian version suffix."""
    assert packages._normalize_debian_name("bzip2") == "bzip2"


def test_normalize_debian_name_leaves_libxml2_alone() -> None:
    """`2` is part of libxml2's own name, not a Debian version suffix."""
    assert packages._normalize_debian_name("libxml2") == "libxml2"


def test_normalize_debian_name_leaves_lz4_alone() -> None:
    """`4` is part of lz4's own name, not a Debian version suffix."""
    assert packages._normalize_debian_name("lz4") == "lz4"


def _upstream(identity: str) -> RemoteRepoSource:
    return RemoteRepoSource.from_identifier("tealdeer", identity)


def test_verify_external_page_matches_through_converging_canonical_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`dbrgn/tealdeer` and `tealdeer-rs/tealdeer` both resolve to repository
    48739367 on GitHub: an org migration, not two projects, so equal
    canonical IDs prove sameness and fall through to the version
    comparison (ADR-0055 phase 2)."""
    monkeypatch.setattr(
        packages.subprocess,
        "run",
        _dpkg(
            owner="tmux:amd64",
            version="1.9.0-1",
            source="tmux",
            homepage="https://github.com/dbrgn/tealdeer/",
        ),
    )
    monkeypatch.setattr(
        packages, "canonical_github_repository_id", lambda identity: 48739367
    )

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tldr.1.gz"),
        package="tealdeer",
        version="1.9.0",
        upstream=_upstream("tealdeer-rs/tealdeer"),
    )

    assert result.freshness is packages.ExternalPageFreshness.MATCH


def test_verify_external_page_wrong_owner_on_distinct_canonical_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two GitHub repositories that resolve to different canonical IDs are
    positive disproof of sameness -- the only path to `WRONG_OWNER`."""
    monkeypatch.setattr(
        packages.subprocess,
        "run",
        _dpkg(
            owner="tmux:amd64",
            version="1.9.0-1",
            source="tmux",
            homepage="https://github.com/tmux/tmux",
        ),
    )
    ids = {"tmux/tmux": 1, "tealdeer-rs/tealdeer": 2}
    monkeypatch.setattr(
        packages, "canonical_github_repository_id", lambda identity: ids[identity]
    )

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tldr.1.gz"),
        package="tealdeer",
        version="1.9.0",
        upstream=_upstream("tealdeer-rs/tealdeer"),
    )

    assert result.freshness is packages.ExternalPageFreshness.WRONG_OWNER
    assert result.owner == "tmux:amd64"


def test_verify_external_page_unverified_on_canonical_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A network failure never fails the row -- it degrades to `UNVERIFIED`."""
    monkeypatch.setattr(
        packages.subprocess,
        "run",
        _dpkg(
            owner="tmux:amd64",
            version="1.9.0-1",
            source="tmux",
            homepage="https://github.com/tmux/tmux",
        ),
    )
    monkeypatch.setattr(
        packages, "canonical_github_repository_id", lambda identity: None
    )

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tldr.1.gz"),
        package="tealdeer",
        version="1.9.0",
        upstream=_upstream("tealdeer-rs/tealdeer"),
    )

    assert result.freshness is packages.ExternalPageFreshness.UNVERIFIED
    assert result.owner == "tmux:amd64"


def test_verify_external_page_unverified_without_a_debian_homepage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `${Homepage}` on the owner leaves nothing to resolve against."""
    monkeypatch.setattr(
        packages.subprocess,
        "run",
        _dpkg(owner="tmux:amd64", version="1.9.0-1", source="tmux", homepage=""),
    )

    def _unexpected_lookup(identity: str) -> int:
        raise AssertionError("no homepage to resolve")

    monkeypatch.setattr(packages, "canonical_github_repository_id", _unexpected_lookup)

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tldr.1.gz"),
        package="tealdeer",
        version="1.9.0",
        upstream=_upstream("tealdeer-rs/tealdeer"),
    )

    assert result.freshness is packages.ExternalPageFreshness.UNVERIFIED


def test_verify_external_page_unverified_without_an_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row with no resolved upstream at all (e.g. a `core:` mise backend)
    cannot be disproven and stays `UNVERIFIED`."""
    monkeypatch.setattr(
        packages.subprocess,
        "run",
        _dpkg(
            owner="tmux:amd64",
            version="1.9.0-1",
            source="tmux",
            homepage="https://github.com/tmux/tmux",
        ),
    )

    def _unexpected_lookup(identity: str) -> int:
        raise AssertionError("no upstream to resolve")

    monkeypatch.setattr(packages, "canonical_github_repository_id", _unexpected_lookup)

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tldr.1.gz"),
        package="tealdeer",
        version="1.9.0",
        upstream=None,
    )

    assert result.freshness is packages.ExternalPageFreshness.UNVERIFIED
