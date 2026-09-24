"""Tests for `DebianProvider`: dpkg-backed detection and repository-only
upstream identity, against a fake `dpkg-query` (no reliance on the host).
"""

import subprocess
from pathlib import Path

import pytest

from maniac.config import Config
from maniac.models import Installation, RepoSource
from maniac.sources import packages
from maniac.sources.providers import debian
from maniac.sources.providers.registry import ProviderRegistry, registry


@pytest.fixture(autouse=True)
def _clear_debian_caches() -> None:
    packages._debian_bin_owners.cache_clear()
    packages._debian_version.cache_clear()
    packages._debian_homepage.cache_clear()
    packages._debian_copyright_source.cache_clear()
    packages._debian_source.cache_clear()
    packages._debian_owner.cache_clear()


def _fake_dpkg_s(owned: dict[Path, str]):
    """Fake batched `dpkg-query -S`: one `owner: path` line per owned path,
    mirroring the real command's silence on stdout for unmatched paths.
    """

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        queried = args[3:]  # ["dpkg-query", "-S", "--", *paths]
        lines = [f"{owned[Path(p)]}: {p}" for p in queried if Path(p) in owned]
        returncode = 0 if len(lines) == len(queried) else 1
        return subprocess.CompletedProcess(
            args, returncode, "\n".join(lines) + ("\n" if lines else ""), ""
        )

    return run


def _system_dirs(tmp_path: Path, *names: str) -> frozenset[Path]:
    dirs = []
    for name in names:
        d = tmp_path / name
        d.mkdir(parents=True, exist_ok=True)
        dirs.append(d)
    return frozenset(dirs)


def test_batched_ownership_resolves_a_usrmerge_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/bin` is a symlink to `usr/bin`; dpkg records ownership only under
    the canonical `usr/bin` form, and a lookup through either directory
    entry must still resolve to it."""
    usr_bin = tmp_path / "usr" / "bin"
    usr_bin.mkdir(parents=True)
    real = usr_bin / "curl"
    real.touch()
    bin_dir = tmp_path / "bin"
    bin_dir.symlink_to(usr_bin)

    monkeypatch.setattr(packages, "_SYSTEM_BIN_DIRS", frozenset({bin_dir, usr_bin}))
    monkeypatch.setattr(packages.subprocess, "run", _fake_dpkg_s({real: "curl"}))

    owners = packages._debian_bin_owners()

    assert owners == {real.resolve(): "curl"}
    assert (bin_dir / "curl").resolve() in owners


def test_unowned_system_binary_is_not_claimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usr_bin = tmp_path / "usr" / "bin"
    usr_bin.mkdir(parents=True)
    owned = usr_bin / "curl"
    owned.touch()
    unowned = usr_bin / "my-local-script"
    unowned.touch()

    monkeypatch.setattr(packages, "_SYSTEM_BIN_DIRS", frozenset({usr_bin}))
    monkeypatch.setattr(packages.subprocess, "run", _fake_dpkg_s({owned: "curl"}))

    provider = debian.DebianProvider()

    assert provider.can_detect(owned.resolve()) is True
    assert provider.can_detect(unowned.resolve()) is False
    assert provider.detect(unowned) is None


def test_no_dpkg_yields_no_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usr_bin = tmp_path / "usr" / "bin"
    usr_bin.mkdir(parents=True)
    target = usr_bin / "curl"
    target.touch()

    monkeypatch.setattr(packages, "_SYSTEM_BIN_DIRS", frozenset({usr_bin}))

    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(packages.subprocess, "run", missing)

    provider = debian.DebianProvider()

    assert provider.can_detect(target.resolve()) is False
    assert provider.detect(target) is None


def test_detect_strips_epoch_and_debian_revision_from_the_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usr_bin = tmp_path / "usr" / "bin"
    usr_bin.mkdir(parents=True)
    target = usr_bin / "curl"
    target.touch()

    monkeypatch.setattr(packages, "_SYSTEM_BIN_DIRS", frozenset({usr_bin}))
    monkeypatch.setattr(packages.subprocess, "run", _fake_dpkg_s({target: "curl"}))
    monkeypatch.setattr(
        packages, "_debian_version", lambda package: "2:8.5.0-2ubuntu10.6"
    )

    inst = debian.DebianProvider().detect(target)

    assert inst is not None
    assert inst.provider == "debian"
    assert inst.package == "curl"
    assert inst.version == "8.5.0"
    assert inst.root == Path("/usr")


def test_resolve_source_accepts_a_github_repository_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inst = Installation(
        binary="curl",
        bin_path=Path("/usr/bin/curl"),
        real_path=Path("/usr/bin/curl"),
        provider="debian",
        package="curl",
        version="8.5.0",
        root=Path("/usr"),
    )
    monkeypatch.setattr(
        packages,
        "_debian_copyright_source",
        lambda package: "https://github.com/curl/curl",
    )
    monkeypatch.setattr(
        packages, "_debian_homepage", lambda package: "https://curl.se/"
    )

    source = debian.DebianProvider().resolve_source(
        inst, config=Config(), sources=registry
    )

    assert source == RepoSource(name="curl", target="curl/curl", is_local=False)


def test_resolve_source_refuses_a_website_url(monkeypatch: pytest.MonkeyPatch) -> None:
    inst = Installation(
        binary="curl",
        bin_path=Path("/usr/bin/curl"),
        real_path=Path("/usr/bin/curl"),
        provider="debian",
        package="curl",
        version="8.5.0",
        root=Path("/usr"),
    )
    monkeypatch.setattr(packages, "_debian_copyright_source", lambda package: None)
    monkeypatch.setattr(
        packages, "_debian_homepage", lambda package: "https://curl.se/"
    )

    assert (
        debian.DebianProvider().resolve_source(inst, config=Config(), sources=registry)
        is None
    )


def test_resolve_source_never_reads_vcs_git_or_vcs_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A copyright file with no `Source:` field, only `Vcs-Git`, must not
    fall back to the packaging repository it names."""
    inst = Installation(
        binary="tool",
        bin_path=Path("/usr/bin/tool"),
        real_path=Path("/usr/bin/tool"),
        provider="debian",
        package="tool",
        version="1.0",
        root=Path("/usr"),
    )
    monkeypatch.setattr(packages, "_debian_copyright_source", lambda package: None)
    monkeypatch.setattr(packages, "_debian_homepage", lambda package: "")

    assert (
        debian.DebianProvider().resolve_source(inst, config=Config(), sources=registry)
        is None
    )


def test_local_docs_returns_nothing() -> None:
    inst = Installation(
        binary="curl",
        bin_path=Path("/usr/bin/curl"),
        real_path=Path("/usr/bin/curl"),
        provider="debian",
        package="curl",
        version="8.5.0",
        root=Path("/usr"),
    )

    assert debian.DebianProvider().local_docs(inst) == []


def test_user_level_provider_still_wins_over_debian(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Debian registers last: a provider that recognises its own layout on
    the same path is tried first and wins."""
    usr_bin = tmp_path / "usr" / "bin"
    usr_bin.mkdir(parents=True)
    target = usr_bin / "tool"
    target.touch()

    monkeypatch.setattr(packages, "_SYSTEM_BIN_DIRS", frozenset({usr_bin}))
    monkeypatch.setattr(packages.subprocess, "run", _fake_dpkg_s({target: "tool"}))

    class _FakeUserProvider:
        name = "fake-user"

        def can_detect(self, real_path: Path) -> bool:
            return real_path == target.resolve()

        def detect(self, bin_path: Path) -> Installation | None:
            return Installation(
                binary=bin_path.name,
                bin_path=bin_path,
                real_path=bin_path.resolve(),
                provider=self.name,
                package="tool",
                version="1.0",
                root=bin_path.parent,
            )

        def resolve_source(self, inst, *, config, sources):
            return None

        def local_docs(self, inst):
            return []

    reg = ProviderRegistry()
    reg.register(_FakeUserProvider())
    reg.register(debian.DebianProvider())

    candidates = list(reg.candidates_for(target))
    assert [c.name for c in candidates] == ["fake-user", "debian"]

    winner = None
    for candidate in candidates:
        winner = candidate.detect(target)
        if winner is not None:
            break

    assert winner is not None
    assert winner.provider == "fake-user"


def test_registry_registers_debian_last() -> None:
    assert [provider.name for provider in registry][-1] == "debian"
