"""Tests for external manpage package provenance."""

import subprocess
from pathlib import Path

import pytest

from maniac.sources import packages


@pytest.fixture(autouse=True)
def _clear_debian_caches() -> None:
    packages._debian_owner.cache_clear()
    packages._debian_version.cache_clear()


def _result(
    args: list[str], *, owner: str, version: str
) -> subprocess.CompletedProcess[str]:
    if "-S" in args:
        return subprocess.CompletedProcess(
            args, 0, f"{owner}: /usr/share/man/man1/tldr.1.gz\n", ""
        )
    return subprocess.CompletedProcess(args, 0, f"{version}\n", "")


def test_verify_external_page_marks_tldr_shaped_debian_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        packages.subprocess,
        "run",
        lambda args, **kwargs: _result(
            args, owner="tealdeer:amd64", version="1.6.1-4build2"
        ),
    )

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tldr.1.gz"), package="tealdeer", version="1.9.0"
    )

    assert result is packages.ExternalPageFreshness.MISMATCH


def test_verify_external_page_accepts_matching_debian_epoch_and_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        packages.subprocess,
        "run",
        lambda args, **kwargs: _result(
            args, owner="tealdeer:amd64", version="2:1.9.0-1ubuntu1"
        ),
    )

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tldr.1.gz"), package="tealdeer", version="1.9.0"
    )

    assert result is packages.ExternalPageFreshness.MATCH


def test_verify_external_page_rejects_different_package_without_binary_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return _result(args, owner="other-package:amd64", version="1.9.0-1")

    monkeypatch.setattr(packages.subprocess, "run", run)

    result = packages.verify_external_page(
        Path("/usr/share/man/man1/tldr.1.gz"), package="tealdeer", version="1.9.0"
    )

    assert result is packages.ExternalPageFreshness.UNVERIFIED
    assert len(calls) == 1


def test_verify_external_page_degrades_when_dpkg_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(packages.subprocess, "run", missing)

    assert (
        packages.verify_external_page(
            Path("/usr/share/man/man1/tldr.1.gz"), package="tealdeer", version="1.9.0"
        )
        is packages.ExternalPageFreshness.UNVERIFIED
    )


def test_verify_external_page_caches_owner_and_version_subprocesses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return _result(args, owner="tealdeer:amd64", version="1.9.0-1")

    monkeypatch.setattr(packages.subprocess, "run", run)
    page = Path("/usr/share/man/man1/tldr.1.gz")

    assert (
        packages.verify_external_page(page, package="tealdeer", version="1.9.0")
        is packages.ExternalPageFreshness.MATCH
    )
    assert (
        packages.verify_external_page(page, package="tealdeer", version="1.9.0")
        is packages.ExternalPageFreshness.MATCH
    )
    assert len(calls) == 2
