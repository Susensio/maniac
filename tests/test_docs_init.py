from pathlib import Path

import pytest

from maniac.models import RepoSource
from maniac.sources.docs import (
    cache,
    discover_repo_manpages,
    pages,
    release,
    repository,
)

# These exercise the probe cache orchestration in maniac/sources/docs/__init__.py
# itself (discover_repo_manpages' cache._cache_lock / pages._read_probe_cache /
# pages._write_probe_cache dance), not any one submodule's internals -- the
# facade owns that mechanism, so it is the single best fit despite touching
# repository and release only through monkeypatches.


def test_discovery_resolves_a_tag_once_before_tree_and_release_probes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lookups = 0

    def find_tag(*args: object) -> tuple[str, bool]:
        nonlocal lookups
        lookups += 1
        return "v1.2.3", True

    monkeypatch.setattr(repository, "_find_matching_tag", find_tag)
    monkeypatch.setattr(
        repository,
        "_discover_remote_manpage_result",
        lambda *args: pages._ProbeResult([], True),
    )
    monkeypatch.setattr(
        release,
        "_discover_github_release_manpages_result",
        lambda *args: pages._ProbeResult([], True),
    )
    source = RepoSource(name="tool", target="owner/tool", is_local=False)

    assert discover_repo_manpages(source, "tool", tmp_path, version="1.2.3")[0] == []
    assert lookups == 1


def test_versioned_probe_cache_skips_network_for_positive_and_negative_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    page = tmp_path / "manpages" / "key" / "tool.1"
    page.parent.mkdir(parents=True)
    page.write_text(".TH TOOL 1\n", encoding="utf-8")
    monkeypatch.setattr(
        repository, "_find_matching_tag", lambda *args: ("v1.2.3", True)
    )
    monkeypatch.setattr(
        repository,
        "_discover_remote_manpage_result",
        lambda *args: pages._ProbeResult([page], True),
    )
    assert discover_repo_manpages(source, "tool", tmp_path, version="1.2.3")[0] == [
        page
    ]

    monkeypatch.setattr(
        repository, "_find_matching_tag", lambda *args: pytest.fail("network used")
    )
    monkeypatch.setattr(
        repository,
        "_discover_remote_manpage_result",
        lambda *args: pytest.fail("network used"),
    )
    assert discover_repo_manpages(source, "tool", tmp_path, version="1.2.3")[0] == [
        page
    ]

    missing = RepoSource(name="missing", target="owner/missing", is_local=False)
    monkeypatch.setattr(
        repository,
        "_find_matching_tag_cached_result",
        lambda *args: (None, True),
    )
    assert (
        discover_repo_manpages(missing, "missing", tmp_path, version="1.2.3")[0] == []
    )
    monkeypatch.setattr(
        repository,
        "_find_matching_tag_cached_result",
        lambda *args: pytest.fail("network used"),
    )
    assert (
        discover_repo_manpages(missing, "missing", tmp_path, version="1.2.3")[0] == []
    )


def test_definitive_versioned_probe_miss_skips_tree_and_release_on_second_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    tree_calls = 0
    release_calls = 0

    def tree(*args: object) -> object:
        nonlocal tree_calls
        tree_calls += 1
        return pages._ProbeResult([], True)

    def release_probe(*args: object) -> object:
        nonlocal release_calls
        release_calls += 1
        return pages._ProbeResult([], True)

    monkeypatch.setattr(
        repository, "_find_matching_tag", lambda *args: ("v1.2.3", True)
    )
    monkeypatch.setattr(repository, "_discover_remote_manpage_result", tree)
    monkeypatch.setattr(
        release, "_discover_github_release_manpages_result", release_probe
    )

    assert discover_repo_manpages(source, "tool", tmp_path, version="1.2.3")[0] == []
    assert (tree_calls, release_calls) == (1, 1)

    monkeypatch.setattr(
        repository,
        "_discover_remote_manpage_result",
        lambda *args: pytest.fail("tree used"),
    )
    monkeypatch.setattr(
        release,
        "_discover_github_release_manpages_result",
        lambda *args: pytest.fail("release used"),
    )
    assert discover_repo_manpages(source, "tool", tmp_path, version="1.2.3")[0] == []


def test_expired_definitive_probe_miss_refreshes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = 1000.0
    calls = 0

    def tree(*args: object) -> object:
        nonlocal calls
        calls += 1
        return pages._ProbeResult([], True)

    monkeypatch.setattr(cache.time, "time", lambda: now)
    monkeypatch.setattr(
        repository, "_find_matching_tag", lambda *args: ("v1.2.3", True)
    )
    monkeypatch.setattr(repository, "_discover_remote_manpage_result", tree)
    monkeypatch.setattr(
        release,
        "_discover_github_release_manpages_result",
        lambda *args: pages._ProbeResult([], True),
    )
    source = RepoSource(name="tool", target="owner/tool", is_local=False)

    assert discover_repo_manpages(source, "tool", tmp_path, version="1.2.3")[0] == []
    now += cache._DEFINITIVE_ABSENCE_TTL + 1
    assert discover_repo_manpages(source, "tool", tmp_path, version="1.2.3")[0] == []
    assert calls == 2


def test_transient_probe_failure_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tree_calls = 0

    def tree(*args: object) -> object:
        nonlocal tree_calls
        tree_calls += 1
        return pages._ProbeResult([], False)

    monkeypatch.setattr(
        repository, "_find_matching_tag", lambda *args: ("v1.2.3", True)
    )
    monkeypatch.setattr(repository, "_discover_remote_manpage_result", tree)
    monkeypatch.setattr(
        release,
        "_discover_github_release_manpages_result",
        lambda *args: pages._ProbeResult([], False),
    )
    source = RepoSource(name="tool", target="owner/tool", is_local=False)

    assert discover_repo_manpages(source, "tool", tmp_path, version="1.2.3")[0] == []
    assert discover_repo_manpages(source, "tool", tmp_path, version="1.2.3")[0] == []
    assert tree_calls == 2


def test_tree_failure_does_not_cache_a_definitive_release_miss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tree_calls = 0
    release_calls = 0

    def tree(*args: object) -> object:
        nonlocal tree_calls
        tree_calls += 1
        return pages._ProbeResult([], False)

    def release_probe(*args: object) -> object:
        nonlocal release_calls
        release_calls += 1
        return pages._ProbeResult([], True)

    monkeypatch.setattr(
        repository, "_find_matching_tag", lambda *args: ("v1.2.3", True)
    )
    monkeypatch.setattr(repository, "_discover_remote_manpage_result", tree)
    monkeypatch.setattr(
        release, "_discover_github_release_manpages_result", release_probe
    )
    source = RepoSource(name="tool", target="owner/tool", is_local=False)

    assert discover_repo_manpages(source, "tool", tmp_path, version="1.2.3")[0] == []
    assert discover_repo_manpages(source, "tool", tmp_path, version="1.2.3")[0] == []
    assert (tree_calls, release_calls) == (2, 2)


def test_malformed_release_metadata_does_not_cache_the_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tree_calls = 0

    def tree(*args: object) -> object:
        nonlocal tree_calls
        tree_calls += 1
        return pages._ProbeResult([], True)

    monkeypatch.setattr(
        repository, "_find_matching_tag", lambda *args: ("v1.2.3", True)
    )
    monkeypatch.setattr(repository, "_discover_remote_manpage_result", tree)
    monkeypatch.setattr(cache, "_download", lambda *args: (b"[]", True))
    source = RepoSource(name="tool", target="owner/tool", is_local=False)

    assert discover_repo_manpages(source, "tool", tmp_path, version="1.2.3")[0] == []
    assert discover_repo_manpages(source, "tool", tmp_path, version="1.2.3")[0] == []
    assert tree_calls == 2


def test_concurrent_definitive_probe_misses_are_single_flight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    calls = 0
    calls_lock = Lock()
    tree_started = Event()
    release_tree = Event()

    def tree(*args: object) -> object:
        nonlocal calls
        with calls_lock:
            calls += 1
        tree_started.set()
        assert release_tree.wait(timeout=2)
        return pages._ProbeResult([], True)

    monkeypatch.setattr(
        repository, "_find_matching_tag", lambda *args: ("v1.2.3", True)
    )
    monkeypatch.setattr(repository, "_discover_remote_manpage_result", tree)
    monkeypatch.setattr(
        release,
        "_discover_github_release_manpages_result",
        lambda *args: pages._ProbeResult([], True),
    )
    source = RepoSource(name="tool", target="owner/tool", is_local=False)

    with ThreadPoolExecutor(max_workers=4) as executor:
        first = executor.submit(
            discover_repo_manpages, source, "tool", tmp_path, None, "1.2.3"
        )
        assert tree_started.wait(timeout=2)
        rest = [
            executor.submit(
                discover_repo_manpages, source, "tool", tmp_path, None, "1.2.3"
            )
            for _ in range(3)
        ]
        release_tree.set()
        assert first.result()[0] == []
        assert [future.result()[0] for future in rest] == [[], [], []]

    assert calls == 1


def test_concurrent_binaries_share_tag_and_release_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    tag_lookups = 0
    downloads = 0

    def find_tag(*args: object) -> tuple[str, bool]:
        nonlocal tag_lookups
        tag_lookups += 1
        return "v1.2.3", True

    def download(*args: object) -> tuple[bytes, bool]:
        nonlocal downloads
        downloads += 1
        return b'{"assets": []}', True

    monkeypatch.setattr(repository, "_find_matching_tag", find_tag)
    monkeypatch.setattr(
        repository,
        "_discover_remote_manpage_result",
        lambda *args: pages._ProbeResult([], True),
    )
    monkeypatch.setattr(cache, "_download", download)
    source = RepoSource(name="tool", target="owner/tool", is_local=False)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda binary: discover_repo_manpages(
                    source, binary, tmp_path, version="1.2.3"
                )[0],
                ["tool", "tool-a", "tool-b", "tool-c"],
            )
        )

    assert results == [[], [], [], []]
    assert tag_lookups == 1
    assert downloads == 1


def test_malformed_upstream_cache_is_replaced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = RepoSource(name="tool", target="owner/tool", is_local=False)
    assert source.clone_url is not None
    path = cache._upstream_cache_path(
        tmp_path, "probes", "source-uri-v1", source.clone_url, "1.2.3", "tool"
    )
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(repository, "_find_matching_tag", lambda *args: (None, False))

    assert discover_repo_manpages(source, "tool", tmp_path, version="1.2.3")[0] == []
    assert not path.exists()
