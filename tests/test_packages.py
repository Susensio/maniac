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
def _clear_debian_caches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    packages._debian_owner.cache_clear()
    packages._debian_owner_map_reset()
    packages._debian_version.cache_clear()
    packages._debian_source.cache_clear()
    # No test relies on this machine's real dpkg database. Point the map
    # builder at a directory that does not exist so it falls back to
    # nothing (an empty map) by default; tests of the map itself build a
    # real fake directory and override this.
    monkeypatch.setattr(packages, "_DPKG_INFO_DIR", tmp_path / "no-such-info-dir")
    monkeypatch.setattr(
        packages, "_DPKG_DIVERSIONS_FILE", tmp_path / "no-such-diversions"
    )


def _write_list_file(info_dir: Path, package: str, paths: list[str]) -> None:
    """A fake `/var/lib/dpkg/info/<package>.list`, dpkg's own manifest format."""
    info_dir.mkdir(parents=True, exist_ok=True)
    (info_dir / f"{package}.list").write_text("\n".join(paths) + "\n")


def _write_diversions(path: Path, records: list[tuple[str, str, str]]) -> None:
    """A fake `/var/lib/dpkg/diversions`: three lines per record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [line for record in records for line in record]
    path.write_text("\n".join(lines) + "\n")


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


def test_debian_owner_map_parses_a_normal_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    info_dir = tmp_path / "info"
    _write_list_file(
        info_dir, "coreutils", ["/usr", "/usr/bin/ls", "/usr/share/man/man1/ls.1.gz"]
    )
    monkeypatch.setattr(packages, "_DPKG_INFO_DIR", info_dir)

    assert packages._debian_owner_map() == {"/usr/share/man/man1/ls.1.gz": "coreutils"}


def test_debian_owner_map_keeps_the_multiarch_qualifier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    info_dir = tmp_path / "info"
    _write_list_file(
        info_dir, "binutils-common:amd64", ["/usr/share/man/man1/addr2line.1.gz"]
    )
    monkeypatch.setattr(packages, "_DPKG_INFO_DIR", info_dir)

    assert packages._debian_owner_map() == {
        "/usr/share/man/man1/addr2line.1.gz": "binutils-common:amd64"
    }


def test_debian_owner_map_leaves_a_multi_owner_page_ambiguous(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    info_dir = tmp_path / "info"
    _write_list_file(info_dir, "passwd", ["/usr/share/man/man5/passwd.5.gz"])
    _write_list_file(info_dir, "man-db", ["/usr/share/man/man5/passwd.5.gz"])
    monkeypatch.setattr(packages, "_DPKG_INFO_DIR", info_dir)

    assert packages._debian_owner_map() == {"/usr/share/man/man5/passwd.5.gz": None}


def test_debian_owner_map_excludes_a_diverted_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    info_dir = tmp_path / "info"
    _write_list_file(info_dir, "captain", ["/usr/share/man/man1/apturl.1.gz"])
    diversions = tmp_path / "diversions"
    _write_diversions(
        diversions,
        [
            (
                "/usr/share/man/man1/apturl.1.gz",
                "/usr/share/man/man1/apturl.1.gz.distrib",
                "captain",
            )
        ],
    )
    monkeypatch.setattr(packages, "_DPKG_INFO_DIR", info_dir)
    monkeypatch.setattr(packages, "_DPKG_DIVERSIONS_FILE", diversions)

    assert packages._debian_owner_map() == {}


def test_debian_owner_map_is_empty_when_the_info_dir_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(packages, "_DPKG_INFO_DIR", tmp_path / "does-not-exist")

    assert packages._debian_owner_map() == {}


def test_debian_owner_map_is_empty_when_the_info_dir_holds_no_list_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    info_dir = tmp_path / "info"
    info_dir.mkdir()
    (info_dir / "coreutils.md5sums").write_text("not a list file\n")
    monkeypatch.setattr(packages, "_DPKG_INFO_DIR", info_dir)

    assert packages._debian_owner_map() == {}


def test_debian_owner_map_builds_exactly_once_under_concurrent_callers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`list` classifies pages from a thread pool, so several threads can
    all reach a cache miss on the batch map before any of them has stored
    a result. Only the first should actually build it."""
    info_dir = tmp_path / "info"
    _write_list_file(info_dir, "coreutils", ["/usr/share/man/man1/ls.1.gz"])
    monkeypatch.setattr(packages, "_DPKG_INFO_DIR", info_dir)

    calls: list[None] = []
    calls_lock = threading.Lock()
    real_fetch = packages._fetch_debian_owner_map

    def counting_fetch() -> dict[str, str | None]:
        with calls_lock:
            calls.append(None)
        time.sleep(0.05)
        return real_fetch()

    monkeypatch.setattr(packages, "_fetch_debian_owner_map", counting_fetch)

    barrier = threading.Barrier(8)

    def worker() -> dict[str, str | None]:
        barrier.wait()
        return packages._debian_owner_map()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: worker(), range(8)))

    assert len(calls) == 1
    assert all(r == {"/usr/share/man/man1/ls.1.gz": "coreutils"} for r in results)


def test_debian_owner_falls_back_to_the_per_page_query_for_a_diverted_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A diverted page is excluded from the map entirely, so it still gets
    an answer, from the untouched per-page query -- not silently `None`."""
    info_dir = tmp_path / "info"
    _write_list_file(info_dir, "tealdeer", ["/usr/share/man/man1/tldr.1.gz"])
    diversions = tmp_path / "diversions"
    _write_diversions(
        diversions,
        [
            (
                "/usr/share/man/man1/tldr.1.gz",
                "/usr/share/man/man1/tldr.1.gz.distrib",
                "captain",
            )
        ],
    )
    monkeypatch.setattr(packages, "_DPKG_INFO_DIR", info_dir)
    monkeypatch.setattr(packages, "_DPKG_DIVERSIONS_FILE", diversions)

    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 0, "tealdeer: /usr/share/man/man1/tldr.1.gz\n", ""
        )

    monkeypatch.setattr(packages.subprocess, "run", run)

    assert packages._debian_owner("/usr/share/man/man1/tldr.1.gz") == "tealdeer"
    assert calls == [["dpkg-query", "-S", "--", "/usr/share/man/man1/tldr.1.gz"]]


def test_debian_owner_falls_back_to_the_per_page_query_when_the_info_dir_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(packages, "_DPKG_INFO_DIR", tmp_path / "does-not-exist")

    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 0, "tealdeer: /usr/share/man/man1/tldr.1.gz\n", ""
        )

    monkeypatch.setattr(packages.subprocess, "run", run)

    assert packages._debian_owner("/usr/share/man/man1/tldr.1.gz") == "tealdeer"
    assert calls == [["dpkg-query", "-S", "--", "/usr/share/man/man1/tldr.1.gz"]]


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


def test_debian_version_dedupes_concurrent_misses_for_the_same_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`list` classifies rows from a thread pool; several rows can name the
    same package and all reach a cache miss before the first caller has
    stored a result. Only the first should actually shell out."""
    calls: list[list[str]] = []
    calls_lock = threading.Lock()

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        with calls_lock:
            calls.append(args)
        time.sleep(0.05)
        return subprocess.CompletedProcess(args, 0, "1.9.0-1\n", "")

    monkeypatch.setattr(packages.subprocess, "run", run)

    barrier = threading.Barrier(8)

    def worker() -> str | None:
        barrier.wait()
        return packages._debian_version("tealdeer")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: worker(), range(8)))

    assert len(calls) == 1
    assert all(r == "1.9.0-1" for r in results)


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
