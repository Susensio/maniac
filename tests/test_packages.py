"""Tests for external manpage package provenance."""

import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from maniac.sources import packages


@pytest.fixture(autouse=True)
def _clear_debian_caches() -> None:
    packages._debian_owner.cache_clear()
    packages._debian_owner_map_reset()
    packages._debian_version.cache_clear()
    packages._debian_source.cache_clear()


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
        Path("/usr/share/man/man1/tldr.1.gz"),
        package="tealdeer",
        version="1.9.0",
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
        Path("/usr/share/man/man1/tldr.1.gz"),
        package="tealdeer",
        version="1.9.0",
    )

    assert result.freshness is packages.ExternalPageFreshness.MATCH


def test_verify_external_page_reaches_unverified_when_sameness_is_unproven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dpkg naming an owner that normalization cannot tie to the provider's
    own package is `UNVERIFIED`: dpkg found evidence that does not decide
    the case, not evidence of a wrong owner. The owner still surfaces
    (ADR-0056)."""
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return _dpkg(owner="tmux:amd64", source="tmux")(args, **kwargs)

    monkeypatch.setattr(packages.subprocess, "run", run)

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tldr.1.gz"),
        package="tealdeer",
        version="1.9.0",
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
        Path("/usr/share/man/man1/tldr.1.gz"),
        package="tealdeer",
        version="1.9.0",
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
        Path("/usr/share/man/man1/tldr.1.gz"),
        package="tealdeer",
        version="1.9.0",
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
        Path("/usr/share/man/man1/tldr.1.gz"),
        package="tealdeer",
        version=None,
    )

    assert result.freshness is packages.ExternalPageFreshness.UNVERIFIED
    assert result.owner is None
    assert calls == []


def test_debian_owner_map_parses_a_normal_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 0, "coreutils: /usr/share/man/man1/ls.1.gz\n", ""
        )

    monkeypatch.setattr(packages.subprocess, "run", run)

    assert packages._debian_owner_map() == {"/usr/share/man/man1/ls.1.gz": "coreutils"}


def test_debian_owner_map_leaves_a_multi_owner_page_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 0, "passwd, man-db: /usr/share/man/man5/passwd.5.gz\n", ""
        )

    monkeypatch.setattr(packages.subprocess, "run", run)

    assert packages._debian_owner_map() == {"/usr/share/man/man5/passwd.5.gz": None}


def test_debian_owner_map_excludes_a_diverted_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            0,
            "diversion by captain from: /usr/share/man/man1/apturl.1.gz\n"
            "diversion by captain to: /usr/share/man/man1/apturl.1.gz.distrib\n"
            "captain: /usr/share/man/man1/apturl.1.gz\n",
            "",
        )

    monkeypatch.setattr(packages.subprocess, "run", run)

    assert packages._debian_owner_map() == {}


def test_debian_owner_map_excludes_a_locally_diverted_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            0,
            "local diversion from: /usr/share/man/man1/foo.1.gz\n"
            "local diversion to: /usr/share/man/man1/foo.1.gz.distrib\n",
            "",
        )

    monkeypatch.setattr(packages.subprocess, "run", run)

    assert packages._debian_owner_map() == {}


def test_debian_owner_map_is_empty_when_nothing_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 1, "", "dpkg-query: no path found matching pattern\n"
        )

    monkeypatch.setattr(packages.subprocess, "run", run)

    assert packages._debian_owner_map() == {}


def test_debian_owner_map_is_empty_without_dpkg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(packages.subprocess, "run", missing)

    assert packages._debian_owner_map() == {}


def test_debian_owner_map_runs_the_dpkg_query_exactly_once_under_concurrent_callers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`list` classifies pages from a thread pool, so several threads can
    all reach a cache miss on the batch map before any of them has stored
    a result. Only the first should actually shell out."""
    calls: list[list[str]] = []
    calls_lock = threading.Lock()

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        with calls_lock:
            calls.append(args)
        time.sleep(0.05)
        return subprocess.CompletedProcess(
            args, 0, "coreutils: /usr/share/man/man1/ls.1.gz\n", ""
        )

    monkeypatch.setattr(packages.subprocess, "run", run)

    barrier = threading.Barrier(8)

    def worker() -> dict[str, str | None]:
        barrier.wait()
        return packages._debian_owner_map()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: worker(), range(8)))

    assert len(calls) == 1
    assert all(r == {"/usr/share/man/man1/ls.1.gz": "coreutils"} for r in results)


def test_debian_owner_falls_back_to_the_per_page_query_for_a_page_outside_the_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page the batch map does not cover (a diverted page, or one outside
    every manpath root) still gets an answer, from the untouched per-page
    query -- not silently `None`."""
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if "-S" in args and "--" not in args:
            return subprocess.CompletedProcess(
                args, 0, "coreutils: /usr/share/man/man1/ls.1.gz\n", ""
            )
        return subprocess.CompletedProcess(
            args, 0, "tealdeer: /usr/share/man/man1/tldr.1.gz\n", ""
        )

    monkeypatch.setattr(packages.subprocess, "run", run)

    assert packages._debian_owner("/usr/share/man/man1/tldr.1.gz") == "tealdeer"
    assert len(calls) == 2
    assert calls[0][:3] == ["dpkg-query", "-S", "/usr/share/man/*"]
    assert calls[1] == ["dpkg-query", "-S", "--", "/usr/share/man/man1/tldr.1.gz"]


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
    used to false-positive as a mismatched name are the same software,
    reaching the version comparison instead."""
    monkeypatch.setattr(
        packages.subprocess,
        "run",
        _dpkg(owner=owner, version="1.9.0-1", source=source),
    )

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tool.1"),
        package=package,
        version="1.9.0",
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
