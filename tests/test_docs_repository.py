import subprocess
from pathlib import Path
from typing import Any

import pytest

from maniac.config import Config
from maniac.models import RepoSource
from maniac.sources.docs import (
    cache,
    discover_repo_manpage,
    fetch_and_extract_docs,
    repository,
)
from maniac.sources.docs.pages import discovered_manpage_uri
from maniac.sources.docs.repository import resolve_repo_dir


def _init_cached_repo(path: Path, clone_url: str) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", clone_url],
        check=True,
    )


def test_fetch_and_extract_docs_includes_github_wiki(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_clone(
        clone_url: str, dest_dir: Path, cfg: object, **kwargs: object
    ) -> bool:
        dest_dir.mkdir()
        (dest_dir / ".git").mkdir()
        if clone_url.endswith(".wiki.git"):
            (dest_dir / "Home.md").write_text("# Wiki", encoding="utf-8")
        else:
            (dest_dir / "README.md").write_text("# Repo", encoding="utf-8")
        return True

    monkeypatch.setattr(repository, "_clone_repository", fake_clone)
    source = RepoSource(name="tool", target="owner/tool", is_local=False)

    doc_files, matched = fetch_and_extract_docs(source, cache_dir=tmp_path)

    assert matched is False
    assert [(doc.rel_path, doc.content) for doc in doc_files] == [
        ("README.md", "# Repo"),
        ("wiki/Home.md", "# Wiki"),
    ]


def test_fetch_and_extract_docs_limits_repo_and_wiki_to_total_characters(
    tmp_path: Path,
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    assert source.clone_url is not None
    repo_dir = tmp_path / "tool"
    _init_cached_repo(repo_dir, source.clone_url)
    (repo_dir / "README.md").write_text("12345678", encoding="utf-8")
    wiki_dir = tmp_path / "tool.wiki"
    wiki_dir.mkdir()
    (wiki_dir / ".git").mkdir()
    (wiki_dir / "Home.md").write_text("abcdefgh", encoding="utf-8")
    doc_files, matched = fetch_and_extract_docs(
        source, cache_dir=tmp_path, max_total_chars=10
    )

    assert matched is False
    assert [doc.rel_path for doc in doc_files] == ["README.md", "wiki/Home.md"]
    assert sum(len(doc.content) for doc in doc_files) == 10


def test_fetch_and_extract_docs_git_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.SubprocessError("Git clone failed")

    monkeypatch.setattr(subprocess, "run", fake_run)
    source = RepoSource(
        name="remote_tool",
        target="test/remote_tool",
        is_local=False,
    )
    docs, matched = fetch_and_extract_docs(source, cache_dir=tmp_path)
    assert docs == []
    assert matched is False


def test_discover_repo_manpage_uses_a_bare_cache_without_clone_or_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    calls: list[list[str]] = []
    fetched = False

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        nonlocal fetched
        calls.append(cmd)
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0 if fetched else 1, stdout="ref\n")
        if "fetch" in cmd:
            fetched = True
            return subprocess.CompletedProcess(cmd, 0, stdout="")
        if "ls-tree" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="tests/" + "x" * 300 + "/fixture\nman/tool.1\n",
            )
        if "show" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=b".TH TOOL 1\n")
        if "remote" in cmd and "get-url" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="https://github.com/owner/tool.git\n"
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    source = RepoSource(name="tool", target="owner/tool", is_local=False)

    manpage = discover_repo_manpage(source, "tool", cache_dir=tmp_path)
    assert manpage is not None
    assert manpage.read_text(encoding="utf-8") == ".TH TOOL 1\n"
    assert discovered_manpage_uri(manpage) == (
        "https://github.com/owner/tool/blob/HEAD/man/tool.1"
    )
    discover_repo_manpage(source, "tool", cache_dir=tmp_path)
    assert not any(command[1] in {"clone", "checkout"} for command in calls)
    assert sum("fetch" in command for command in calls) == 1
    assert not any(len(path.name) > 255 for path in tmp_path.rglob("*"))


def test_find_matching_tag_prefers_v_prefixed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`gh` tags releases `v2.90.0`; a peeled annotated-tag ref (`^{}`) is stripped."""
    import subprocess

    from maniac.sources.docs.repository import _find_matching_tag

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert cmd[:3] == ["git", "ls-remote", "--tags"]
        stdout = "abc\trefs/tags/v2.90.0\ndef\trefs/tags/v2.90.0^{}\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    tag = _find_matching_tag("https://github.com/cli/cli.git", "2.90.0", 10)
    assert tag == "v2.90.0"


def test_find_matching_tag_bare_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """`pandoc` tags releases bare, `3.10.2` with no `v` prefix."""
    import subprocess

    from maniac.sources.docs.repository import _find_matching_tag

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd, 0, stdout="abc\trefs/tags/3.10.2\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    tag = _find_matching_tag("https://github.com/jgm/pandoc.git", "3.10.2", 10)
    assert tag == "3.10.2"


def test_find_matching_tag_no_match_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from maniac.sources.docs.repository import _find_matching_tag

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd, 0, stdout="abc\trefs/tags/v0.1.0\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _find_matching_tag("https://github.com/owner/tool.git", "9.9.9", 10) is None


def test_expired_negative_tag_cache_refreshes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = 1000.0
    lookups = 0

    def fake_time() -> float:
        return now

    def find_tag(*args: object) -> None:
        nonlocal lookups
        lookups += 1

    monkeypatch.setattr(cache.time, "time", fake_time)
    monkeypatch.setattr(repository, "_find_matching_tag", find_tag)
    cfg = Config(cache_dir=tmp_path)
    assert repository._find_matching_tag_cached(tmp_path, "url", "1.2.3", cfg) is None
    now += cache._DEFINITIVE_ABSENCE_TTL + 1
    assert repository._find_matching_tag_cached(tmp_path, "url", "1.2.3", cfg) is None
    assert lookups == 2


def test_transient_tag_lookup_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = 0

    def fail(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise subprocess.TimeoutExpired("git", 1)

    monkeypatch.setattr(repository.subprocess, "run", fail)
    cfg = Config(cache_dir=tmp_path)
    assert repository._find_matching_tag_cached(tmp_path, "url", "1.2.3", cfg) is None
    assert repository._find_matching_tag_cached(tmp_path, "url", "1.2.3", cfg) is None
    assert calls == 2


def test_nonzero_tag_lookup_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = 0

    def fail(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="offline")

    monkeypatch.setattr(repository.subprocess, "run", fail)
    cfg = Config(cache_dir=tmp_path)
    assert repository._find_matching_tag_cached(tmp_path, "url", "1.2.3", cfg) is None
    assert repository._find_matching_tag_cached(tmp_path, "url", "1.2.3", cfg) is None
    assert calls == 2


def test_resolve_repo_dir_with_version_clones_the_matching_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from maniac.config import Config

    monkeypatch.setattr(
        repository, "_find_matching_tag", lambda url, version, timeout: "v1.2.3"
    )
    observed_refs: list[object] = []

    def fake_clone(
        clone_url: str, dest_dir: Path, cfg: object, **kwargs: object
    ) -> bool:
        observed_refs.append(kwargs.get("ref"))
        dest_dir.mkdir()
        (dest_dir / ".git").mkdir()
        return True

    monkeypatch.setattr(repository, "_clone_repository", fake_clone)
    source = RepoSource(name="tool", target="owner/tool", is_local=False)

    result = resolve_repo_dir(source, tmp_path, Config(), version="1.2.3")

    assert result == tmp_path / "tool@v1.2.3"
    assert observed_refs == ["v1.2.3"]


def test_resolve_repo_dir_with_version_no_matching_tag_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0016: an unmatched version is refused, not approximated from the default branch."""
    from maniac.config import Config

    monkeypatch.setattr(
        repository, "_find_matching_tag", lambda url, version, timeout: None
    )
    source = RepoSource(name="tool", target="owner/tool", is_local=False)

    assert resolve_repo_dir(source, tmp_path, Config(), version="9.9.9") is None


def test_fetch_and_extract_docs_with_version_matched_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0019: a matching upstream tag makes the docs version-matched."""
    monkeypatch.setattr(
        repository, "_find_matching_tag", lambda url, version, timeout: "v1.2.3"
    )

    def fake_clone(
        clone_url: str, dest_dir: Path, cfg: object, **kwargs: object
    ) -> bool:
        dest_dir.mkdir()
        (dest_dir / ".git").mkdir()
        (dest_dir / "README.md").write_text("# Tagged", encoding="utf-8")
        return True

    monkeypatch.setattr(repository, "_clone_repository", fake_clone)
    monkeypatch.setattr(repository, "_fetch_github_wiki_docs", lambda *a, **kw: [])
    source = RepoSource(name="tool", target="owner/tool", is_local=False)

    doc_files, matched = fetch_and_extract_docs(
        source, cache_dir=tmp_path, version="1.2.3"
    )

    assert matched is True
    assert [d.rel_path for d in doc_files] == ["README.md"]


def test_fetch_and_extract_docs_with_version_falls_back_when_unmatched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0019: tier 3 falls back to the default branch on no matching tag --
    unlike tier 2's `resolve_repo_dir`, which refuses -- and reports `matched`
    False so the caller records no version rather than stamping one nobody
    checked.
    """
    monkeypatch.setattr(
        repository, "_find_matching_tag", lambda url, version, timeout: None
    )

    def fake_clone(
        clone_url: str, dest_dir: Path, cfg: object, **kwargs: object
    ) -> bool:
        dest_dir.mkdir()
        (dest_dir / ".git").mkdir()
        (dest_dir / "README.md").write_text("# Default branch", encoding="utf-8")
        return True

    monkeypatch.setattr(repository, "_clone_repository", fake_clone)
    monkeypatch.setattr(repository, "_fetch_github_wiki_docs", lambda *a, **kw: [])
    source = RepoSource(name="tool", target="owner/tool", is_local=False)

    doc_files, matched = fetch_and_extract_docs(
        source, cache_dir=tmp_path, version="9.9.9"
    )

    assert matched is False
    assert [d.rel_path for d in doc_files] == ["README.md"]


def test_discover_repo_manpage_with_version_fetches_the_exact_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        repository, "_find_matching_tag", lambda url, version, timeout: "v1.2.3"
    )

    fetched: list[list[str]] = []

    def fake_fetch(
        repo: Path, clone_url: str, ref_name: str, fetch_ref: str, cfg: object
    ) -> str:
        fetched.append([ref_name, fetch_ref])
        return "refs/maniac/tag"

    monkeypatch.setattr(repository, "_fetch_bare_ref", fake_fetch)
    monkeypatch.setattr(repository, "_git_stdout", lambda *args: "tool.1\n")
    monkeypatch.setattr(repository, "_git_bytes", lambda *args: b".TH TOOL 1\n")
    source = RepoSource(name="tool", target="owner/tool", is_local=False)

    manpage = discover_repo_manpage(source, "tool", cache_dir=tmp_path, version="1.2.3")

    assert manpage is not None
    assert fetched == [["v1.2.3", "refs/tags/v1.2.3"]]
    assert discovered_manpage_uri(manpage) == (
        "https://github.com/owner/tool/blob/v1.2.3/tool.1"
    )


def test_clone_configures_sparse_checkout_for_nested_documentation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(repository.subprocess, "run", fake_run)

    assert repository._clone_repository(
        "https://example.test/tool.git", tmp_path, Config()
    )
    assert calls[1][3:6] == ["sparse-checkout", "set", "--no-cone"]
    assert {"docs", "documentation", "man", "manpages"} <= set(calls[1])


@pytest.mark.parametrize("tag", ["v1.2.3", "1.2.3"])
def test_resolve_repo_dir_reuses_a_valid_versioned_cache_without_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tag: str
) -> None:
    from maniac.config import Config

    cached_dir = tmp_path / f"tool@{tag}"
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    assert source.clone_url is not None
    _init_cached_repo(cached_dir, source.clone_url)

    monkeypatch.setattr(
        repository,
        "_find_matching_tag",
        lambda *args: pytest.fail("warm cache must skip ls-remote"),
    )
    monkeypatch.setattr(
        repository,
        "_clone_repository",
        lambda *args, **kwargs: pytest.fail("warm cache must skip cloning"),
    )

    assert resolve_repo_dir(source, tmp_path, Config(), version="1.2.3") == cached_dir


def test_resolve_repo_dir_discards_a_malformed_versioned_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from maniac.config import Config

    malformed_dir = tmp_path / "tool@v1.2.3"
    malformed_dir.mkdir()
    (malformed_dir / "stray").write_text("not a checkout", encoding="utf-8")
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    monkeypatch.setattr(repository, "_find_matching_tag", lambda *args: "1.2.3")

    def fake_clone(
        clone_url: str, dest_dir: Path, cfg: object, **kwargs: object
    ) -> bool:
        assert not malformed_dir.exists()
        dest_dir.mkdir()
        (dest_dir / ".git").mkdir()
        return True

    monkeypatch.setattr(repository, "_clone_repository", fake_clone)

    assert resolve_repo_dir(source, tmp_path, Config(), version="1.2.3") == (
        tmp_path / "tool@1.2.3"
    )


def test_resolve_repo_dir_rejects_cache_from_a_different_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from maniac.config import Config

    cached_dir = tmp_path / "tool@v1.2.3"
    _init_cached_repo(cached_dir, "https://github.com/old/tool.git")
    source = RepoSource(name="tool", target="new/tool", is_local=False)
    monkeypatch.setattr(repository, "_find_matching_tag", lambda *args: "v1.2.3")

    def fake_clone(
        clone_url: str, dest_dir: Path, cfg: object, **kwargs: object
    ) -> bool:
        assert not cached_dir.exists()
        _init_cached_repo(dest_dir, clone_url)
        return True

    monkeypatch.setattr(repository, "_clone_repository", fake_clone)

    assert resolve_repo_dir(source, tmp_path, Config(), version="1.2.3") == cached_dir
