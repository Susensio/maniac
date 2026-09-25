import io
import json
import tarfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Self
from urllib.error import URLError
from urllib.request import Request

import pytest
import zstandard

from maniac.config import Config
from maniac.exceptions import MalformedToolMetadata, ProjectScopedInstall
from maniac.models import Installation
from maniac.sources import discovery, pathcache, resolution
from maniac.sources.discovery import (
    _check_mise_config,
    _clean_git_url,
    _extract_mise_tool_id,
    _load_mise_registry,
    _mise_config_data,
    _mise_config_files,
    _parse_mise_registry,
    _read_mise_registry_archive,
    _resolve_from_mise,
    _run_mise,
)
from maniac.sources.resolution import discover_repo, enumerate_installations


def _compressed_mise_registry(entries: dict[str, bytes]) -> bytes:
    contents = io.BytesIO()
    with tarfile.open(fileobj=contents, mode="w") as archive:
        for name, entry in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(entry)
            archive.addfile(info, io.BytesIO(entry))
    return zstandard.ZstdCompressor().compress(contents.getvalue())


def test_clean_git_url() -> None:
    assert _clean_git_url("https://github.com/gleitz/howdoi.git") == "gleitz/howdoi"
    assert _clean_git_url("git@github.com:astral-sh/uv.git") == "astral-sh/uv"
    assert (
        _clean_git_url("https://github.com/helix-editor/helix") == "helix-editor/helix"
    )
    assert _clean_git_url("https://github.com/d4nj1/TLPUI/") == "d4nj1/TLPUI"


def test_extract_mise_tool_id() -> None:
    p = Path("/home/user/.local/share/mise/installs/glow/2.1.2/glow")
    assert _extract_mise_tool_id(p) == "glow"

    p_non_mise = Path("/usr/local/bin/something")
    assert _extract_mise_tool_id(p_non_mise) is None


def test_check_mise_config_tool_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Path("/home/user/.config/mise/config.toml")
    monkeypatch.setattr(
        discovery,
        "_mise_config_data",
        lambda path: {
            "tool_alias": {"gh-cli": "github:cli/cli", "custom": "owner/custom"}
        },
    )

    assert _check_mise_config(cfg, "gh-cli", "gh") == "cli/cli"
    assert _check_mise_config(cfg, "other", "custom") == "owner/custom"
    assert _check_mise_config(cfg, "unknown", "unknown") is None


def test_check_mise_config_tools_github_and_cargo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Path("/home/user/.config/mise/config.toml")
    monkeypatch.setattr(
        discovery,
        "_mise_config_data",
        lambda path: {
            "tools": {
                "github:sharkdp/fd": "latest",
                "cargo:https://github.com/BurntSushi/ripgrep": "latest",
                "github:junegunn/fzf": {"filter_bins": ["fzf-tmux"]},
            }
        },
    )

    assert _check_mise_config(cfg, "fd", "fd") == "sharkdp/fd"
    assert _check_mise_config(cfg, "ripgrep", "rg") == "BurntSushi/ripgrep"
    assert _check_mise_config(cfg, "unknown", "fzf-tmux") == "junegunn/fzf"
    assert _check_mise_config(cfg, "fmt", "fmt") is None
    assert _check_mise_config(cfg, "od", "od") is None
    assert _check_mise_config(cfg, "unknown", "unknown") is None


def test_check_mise_config_non_table_tool_alias_raises_malformed_tool_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong-shape `tool_alias` (not a table) is reported rather than
    raising an unhandled `AttributeError` that would abort all of `list`."""
    cfg = Path("/home/user/.config/mise/config.toml")
    monkeypatch.setattr(
        discovery, "_mise_config_data", lambda path: {"tool_alias": "oops"}
    )

    with pytest.raises(MalformedToolMetadata) as excinfo:
        _check_mise_config(cfg, "foo", "foo")
    assert excinfo.value.path == cfg


def test_check_mise_config_non_string_alias_target_raises_malformed_tool_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong-shape alias target (not a string) is reported the same way."""
    cfg = Path("/home/user/.config/mise/config.toml")
    monkeypatch.setattr(
        discovery, "_mise_config_data", lambda path: {"tool_alias": {"foo": 1}}
    )

    with pytest.raises(MalformedToolMetadata) as excinfo:
        _check_mise_config(cfg, "foo", "foo")
    assert excinfo.value.path == cfg


def test_mise_config_data_reports_unparseable_mise_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`mise config get -f <path>` itself is trusted for correctness, but a
    reply that is not valid TOML is reported rather than crashing tomllib's
    caller unwrapped (ADR-0060)."""
    cfg = Path("/home/user/.config/mise/config.toml")
    monkeypatch.setattr(discovery, "_run_mise", lambda *args: "not = [toml")
    _mise_config_data.cache_clear()

    with pytest.raises(MalformedToolMetadata) as excinfo:
        _mise_config_data(cfg)
    assert excinfo.value.path == cfg
    _mise_config_data.cache_clear()


def test_mise_config_files_uses_mises_own_tracked_list_not_a_glob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stray `.toml` under mise's config directory that mise itself
    ignores must not be read at all -- confirmed bug: a directory glob
    picked it up and turned it into a broken read for every mise tool that
    falls back to config inspection, even though mise's own environment
    worked fine (`docs/BACKLOG.md`, ADR-0060's test)."""
    tracked = ["/home/user/.config/mise/config.toml"]
    monkeypatch.setattr(
        discovery,
        "_run_mise",
        lambda *args: json.dumps([{"path": p} for p in tracked]),
    )
    _mise_config_files.cache_clear()

    assert _mise_config_files() == (Path(tracked[0]),)
    _mise_config_files.cache_clear()


def test_run_mise_raises_when_mise_binary_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery.shutil, "which", lambda name: None)

    with pytest.raises(MalformedToolMetadata):
        _run_mise("config", "ls", "--json")


def test_discover_repo_fallback(monkeypatch, tmp_path: Path) -> None:
    """A binary nothing on disk resolves to is unresolvable, not a bare-name guess."""
    monkeypatch.setattr(discovery, "_load_mise_registry", dict)
    assert (
        discover_repo("nonexistent_unknown_tool", bin_dir=tmp_path, config=Config())
        is None
    )


def test_discover_repo_has_no_implicit_local_bin_default(
    monkeypatch, tmp_path: Path
) -> None:
    """With no explicit `bin_dir`, resolution is the inherited `$PATH`
    (`pathcache.which`) alone -- a binary sitting where the old default
    pointed, `~/.local/bin`, is not picked up just because it is there.
    """
    fake_home = tmp_path / "home"
    decoy_dir = fake_home / ".local" / "bin"
    decoy_dir.mkdir(parents=True)
    (decoy_dir / "tool").touch()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(pathcache, "which", lambda name: None)

    assert discover_repo("tool", config=Config()) is None


def test_discover_repo_does_not_use_the_registry_without_an_installation(
    monkeypatch, tmp_path: Path
) -> None:
    """ADR-0015's ruling on Stage 2's gap: name-only registry matching is gone
    everywhere. A binary with no detected installation stays unresolved even
    where the registry would have matched it by bare name -- this is
    `discover_repo("envsubst")` ceasing to return "a8m/envsubst".
    """
    monkeypatch.setattr(pathcache, "which", lambda name: None)
    monkeypatch.setattr(
        discovery,
        "_load_mise_registry",
        lambda: {"envsubst": "a8m/envsubst"},
    )

    assert discover_repo("envsubst", bin_dir=tmp_path, config=Config()) is None


def _fake_installation(binary: str, *, provider: str = "fake") -> Installation:
    return Installation(
        binary=binary,
        bin_path=Path(f"/bin/{binary}"),
        real_path=Path(f"/bin/{binary}"),
        provider=provider,
        package=binary,
        version=None,
        root=Path("/root"),
    )


def test_enumerate_installations_walks_path_and_keeps_only_claimed_binaries(
    monkeypatch, tmp_path: Path
) -> None:
    claimed = tmp_path / "claimed"
    claimed.touch(mode=0o755)
    unclaimed = tmp_path / "unclaimed"
    unclaimed.touch(mode=0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    def fake_detect(bin_path: Path):
        if bin_path.name == "claimed":
            return ("fake-provider", _fake_installation("claimed"))
        return None

    monkeypatch.setattr(resolution, "_detect_via_registry", fake_detect)

    found = enumerate_installations()

    assert [inst.binary for _, inst in found] == ["claimed"]


def test_enumerate_installations_resolves_a_name_once_at_its_first_path_entry(
    monkeypatch, tmp_path: Path
) -> None:
    """ADR-0016's tie-break: two providers claiming one name, the first `$PATH` wins."""
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    (first_dir / "tool").touch(mode=0o755)
    (second_dir / "tool").touch(mode=0o755)
    monkeypatch.setenv("PATH", f"{first_dir}:{second_dir}")

    def fake_detect(bin_path: Path):
        return (f"fake-provider-{bin_path.parent.name}", _fake_installation("tool"))

    monkeypatch.setattr(resolution, "_detect_via_registry", fake_detect)

    [(provider, inst)] = enumerate_installations()

    assert (provider, inst.binary) == ("fake-provider-first", "tool")


def test_enumerate_installations_retains_a_later_path_occurrences_claim_as_a_loser(
    monkeypatch, tmp_path: Path
) -> None:
    """A shadowed same-name binary later on `$PATH` is discarded as the winner
    but its provider's claim is retained on the winner's `Installation.losers`.
    """
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    (first_dir / "tool").touch(mode=0o755)
    (second_dir / "tool").touch(mode=0o755)
    monkeypatch.setenv("PATH", f"{first_dir}:{second_dir}")

    def fake_detect(bin_path: Path):
        label = bin_path.parent.name
        return (f"fake-provider-{label}", _fake_installation("tool", provider=label))

    monkeypatch.setattr(resolution, "_detect_via_registry", fake_detect)

    [(provider, inst)] = enumerate_installations()

    assert provider == "fake-provider-first"
    assert inst.provider == "first"
    assert [loser.provider for loser in inst.losers] == ["second"]


def test_enumerate_installations_leaves_losers_empty_with_no_shadow(
    monkeypatch, tmp_path: Path
) -> None:
    claimed = tmp_path / "claimed"
    claimed.touch(mode=0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(
        resolution,
        "_detect_via_registry",
        lambda p: ("fake-provider", _fake_installation(p.name)),
    )

    [(_, inst)] = enumerate_installations()

    assert inst.losers == ()


def test_enumerate_installations_reports_and_drops_a_malformed_candidate(
    monkeypatch, tmp_path: Path
) -> None:
    """`enumerate_installations`'s own try/except (not a fake standing in for
    the whole function) reports a candidate whose provider raises
    `MalformedToolMetadata` through `on_error`, and drops it from the
    result without aborting the rest of the walk (ADR-0060)."""
    broken = tmp_path / "broken"
    broken.touch(mode=0o755)
    ok = tmp_path / "ok"
    ok.touch(mode=0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    error = MalformedToolMetadata(Path("/x/y.toml"), "bad toml")

    def fake_detect(bin_path: Path):
        if bin_path.name == "broken":
            raise error
        return ("fake-provider", _fake_installation("ok"))

    monkeypatch.setattr(resolution, "_detect_via_registry", fake_detect)
    reported: list[tuple[str, MalformedToolMetadata | ProjectScopedInstall]] = []

    found = enumerate_installations(on_error=lambda name, e: reported.append((name, e)))

    assert [inst.binary for _, inst in found] == ["ok"]
    assert reported == [("broken", error)]


def test_enumerate_installations_reports_a_project_scoped_refusal_too(
    monkeypatch, tmp_path: Path
) -> None:
    """`ProjectScopedInstall` (ADR-0061) is caught through the same path as
    `MalformedToolMetadata`, not left to escape unenumerated."""
    refused = tmp_path / "refused"
    refused.touch(mode=0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    error = ProjectScopedInstall("refused", Path("/root"))
    monkeypatch.setattr(
        resolution, "_detect_via_registry", lambda p: (_ for _ in ()).throw(error)
    )
    reported: list[tuple[str, object]] = []

    found = enumerate_installations(on_error=lambda name, e: reported.append((name, e)))

    assert found == []
    assert reported == [("refused", error)]


def test_enumerate_installations_drops_a_later_shadow_that_also_errors(
    monkeypatch, tmp_path: Path
) -> None:
    """A later `$PATH` shadow that also raises is dropped silently -- it
    never wins regardless, and has no row of its own to report against."""
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    (first_dir / "tool").touch(mode=0o755)
    (second_dir / "tool").touch(mode=0o755)
    monkeypatch.setenv("PATH", f"{first_dir}:{second_dir}")

    def fake_detect(bin_path: Path):
        if bin_path.parent.name == "first":
            return ("fake-provider", _fake_installation("tool"))
        raise MalformedToolMetadata(bin_path, "shadow is broken")

    monkeypatch.setattr(resolution, "_detect_via_registry", fake_detect)
    reported: list[tuple[str, MalformedToolMetadata | ProjectScopedInstall]] = []

    found = enumerate_installations(on_error=lambda name, e: reported.append((name, e)))

    assert [inst.binary for _, inst in found] == ["tool"]
    assert reported == []


def test_enumerate_installations_skips_non_executable_files(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / "not_executable").touch(mode=0o644)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(
        resolution, "_detect_via_registry", lambda p: pytest.fail("must not be called")
    )

    assert enumerate_installations() == []


def test_enumerate_installations_on_start_and_on_scan_are_optional_and_no_op_by_default(
    monkeypatch, tmp_path: Path
) -> None:
    """Existing callers omitting the callbacks see unchanged behaviour."""
    claimed = tmp_path / "claimed"
    claimed.touch(mode=0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(
        resolution,
        "_detect_via_registry",
        lambda p: ("fake-provider", _fake_installation(p.name)),
    )

    found = enumerate_installations()

    assert [inst.binary for _, inst in found] == ["claimed"]


def test_enumerate_installations_reports_candidate_count_then_one_scan_per_candidate(
    monkeypatch, tmp_path: Path
) -> None:
    for name in ("one", "two", "three"):
        (tmp_path / name).touch(mode=0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(
        resolution,
        "_detect_via_registry",
        lambda p: ("fake-provider", _fake_installation(p.name)),
    )

    starts: list[int] = []
    scans = 0

    def on_start(total: int) -> None:
        starts.append(total)

    def on_scan() -> None:
        nonlocal scans
        scans += 1

    enumerate_installations(on_start=on_start, on_scan=on_scan)

    assert starts == [3]
    assert scans == 3


def test_resolve_from_mise_checks_all_local_config_before_registry(
    monkeypatch, tmp_path: Path
) -> None:
    config_a = {"tools": {"rg": "latest"}}
    config_b = {"tool_alias": {"rg": "github:private/rg"}}
    monkeypatch.setattr(
        discovery, "_mise_config_files", lambda: (Path("a.toml"), Path("b.toml"))
    )
    monkeypatch.setattr(
        discovery,
        "_mise_config_data",
        lambda path: config_a if path == Path("a.toml") else config_b,
    )
    monkeypatch.setattr(
        discovery,
        "_load_mise_registry",
        lambda cache_path: {"rg": "BurntSushi/ripgrep"},
    )

    assert (
        _resolve_from_mise("rg", "rg", config=Config(config_dir=tmp_path))
        == "private/rg"
    )


def test_resolve_from_mise_offline_skips_the_registry(
    monkeypatch, tmp_path: Path
) -> None:
    """`offline=True` (ADR-0018's `maniac list`) must never reach
    `_query_mise_registry` -- proven by making it raise -- even when local
    config has nothing either.
    """
    monkeypatch.setattr(discovery, "_mise_config_files", lambda: ())

    def fail_if_called(tool: str, *, config: Config) -> str | None:
        raise AssertionError("registry fallback must not run when offline")

    monkeypatch.setattr(discovery, "_query_mise_registry", fail_if_called)

    assert (
        _resolve_from_mise(
            "rg",
            "rg",
            config=Config(config_dir=tmp_path / "empty-config"),
            offline=True,
        )
        is None
    )


def test_parse_mise_registry_resolves_short_names_aliases_and_bins() -> None:
    entry = (
        b'backends = ["aqua:BurntSushi/ripgrep"]\n'
        b'aliases = ["ripgrep-cli"]\n'
        b'bins = ["rg"]\n'
    )
    compressed = _compressed_mise_registry({"registry/ripgrep.toml": entry})

    assert _parse_mise_registry(compressed) == {
        "ripgrep": "BurntSushi/ripgrep",
        "ripgrep-cli": "BurntSushi/ripgrep",
        "rg": "BurntSushi/ripgrep",
    }


def test_parse_mise_registry_prefers_canonical_names_and_normalizes_aqua() -> None:
    compressed = _compressed_mise_registry(
        {
            "registry/aws-copilot.toml": (
                b'backends = ["github:aws/copilot-cli"]\naliases = ["copilot"]\n'
            ),
            "registry/copilot.toml": b'backends = ["github:github/copilot-cli"]\n',
            "registry/kubectl.toml": (
                b'backends = ["aqua:kubernetes/kubernetes/kubectl"]\n'
            ),
        }
    )

    registry = _parse_mise_registry(compressed)

    assert registry["copilot"] == "github/copilot-cli"
    assert registry["kubectl"] == "kubernetes/kubernetes"


def test_malformed_mise_registry_raises_malformed_tool_metadata(monkeypatch) -> None:
    """A corrupt cached registry archive is reported once a rebuild attempt
    also fails to parse, not silently emptied (ADR-0060)."""
    archive = _compressed_mise_registry({"registry/broken.toml": b"\xff"})
    monkeypatch.setattr(
        discovery, "_read_mise_registry_archive", lambda cache_path: archive
    )
    _load_mise_registry.cache_clear()

    cache_path = Path("registry.tar.zst")
    with pytest.raises(MalformedToolMetadata) as excinfo:
        _load_mise_registry(cache_path)
    assert excinfo.value.path == cache_path

    _load_mise_registry.cache_clear()


def test_malformed_mise_registry_content_self_heals_on_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unparseable *content* in a present, readable cache file (distinct
    from `_read_mise_registry_archive`'s own OSError handling, which only
    checks the file is reachable) is deleted and rebuilt once -- a second
    read that returns valid content after the delete succeeds rather than
    raising (ADR-0060 Corrections)."""
    corrupt = _compressed_mise_registry({"registry/broken.toml": b"\xff"})
    valid = _compressed_mise_registry(
        {"registry/ripgrep.toml": b'backends = ["github:BurntSushi/ripgrep"]\n'}
    )
    calls = 0

    def fake_read(cache_path: Path) -> bytes:
        nonlocal calls
        calls += 1
        return corrupt if calls == 1 else valid

    monkeypatch.setattr(discovery, "_read_mise_registry_archive", fake_read)
    _load_mise_registry.cache_clear()

    registry = _load_mise_registry(Path("registry.tar.zst"))

    assert registry == {"ripgrep": "BurntSushi/ripgrep"}
    assert calls == 2

    _load_mise_registry.cache_clear()


def test_mise_registry_load_memoizes_failure_not_only_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Offline plus a corrupt cache retried `_read_mise_registry_archive`
    (10s `urlopen` timeout) once per mise tool looked up in the same
    process, since only a successful load was cached. A failed load is
    memoized too, so a second lookup in the same process raises
    immediately rather than paying the read again."""
    calls = 0

    def failing_read(cache_path: Path) -> bytes:
        nonlocal calls
        calls += 1
        raise MalformedToolMetadata(cache_path, "offline, and the cache is corrupt")

    monkeypatch.setattr(discovery, "_read_mise_registry_archive", failing_read)
    _load_mise_registry.cache_clear()

    cache_path = Path("registry.tar.zst")
    for _ in range(5):
        with pytest.raises(MalformedToolMetadata):
            _load_mise_registry(cache_path)

    assert calls == 1

    _load_mise_registry.cache_clear()


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_read_mise_registry_archive_self_heals_an_unreadable_fresh_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """maniac's own cache is disposable state, not the user's environment
    (ADR-0060 Corrections) -- a present, TTL-fresh cache file that cannot be
    read is deleted and rebuilt from a fresh download rather than reported."""
    cache_path = tmp_path / "mise-registry.tar.zst"
    cache_path.write_bytes(b"stale")
    cache_path.chmod(0o000)
    monkeypatch.setattr(
        discovery, "urlopen", lambda request, timeout: _FakeResponse(b"rebuilt")
    )
    try:
        assert _read_mise_registry_archive(cache_path) == b"rebuilt"
        assert cache_path.read_bytes() == b"rebuilt"
    finally:
        cache_path.chmod(0o644)


def test_read_mise_registry_archive_reports_a_corrupt_cache_only_if_rebuild_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Self-healing gives way to a report only once the rebuild itself fails
    (e.g. offline) -- the corrupt cache is still gone, so a later run can
    retry (ADR-0060 Corrections)."""
    cache_path = tmp_path / "mise-registry.tar.zst"
    cache_path.write_bytes(b"stale")
    cache_path.chmod(0o000)

    def fake_urlopen(request: Request, timeout: int) -> None:
        raise URLError("offline")

    monkeypatch.setattr(discovery, "urlopen", fake_urlopen)
    with pytest.raises(MalformedToolMetadata) as excinfo:
        _read_mise_registry_archive(cache_path)
    assert excinfo.value.path == cache_path
    assert not cache_path.exists()


def test_read_mise_registry_archive_raises_when_the_cache_cannot_even_be_stat_ed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A cache path whose freshness check itself fails (e.g. a directory with
    its execute bit stripped) is reported the same as any other rebuild
    failure -- it must not escape as a bare, unwrapped `OSError` past the
    `MalformedToolMetadata` boundary every caller catches (ADR-0060)."""
    restricted = tmp_path / "restricted"
    restricted.mkdir()
    cache_path = restricted / "mise-registry.tar.zst"
    cache_path.write_bytes(b"stale")
    restricted.chmod(0o000)
    monkeypatch.setattr(
        discovery, "urlopen", lambda request, timeout: _FakeResponse(b"rebuilt")
    )
    try:
        with pytest.raises(MalformedToolMetadata) as excinfo:
            _read_mise_registry_archive(cache_path)
        assert excinfo.value.path == cache_path
    finally:
        restricted.chmod(0o755)


def test_mise_registry_download_sends_user_agent(monkeypatch, tmp_path: Path) -> None:
    observed_request: Request | None = None
    observed_timeout: int | None = None

    class Response:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"registry"

    def fake_urlopen(request: Request, timeout: int) -> Response:
        nonlocal observed_request, observed_timeout
        observed_request = request
        observed_timeout = timeout
        return Response()

    monkeypatch.setattr(discovery, "urlopen", fake_urlopen)

    config = Config(cache_dir=tmp_path / "cache" / "maniac" / "repos")
    cache_path = config.cache_dir.parent / "mise-registry.tar.zst"
    assert _read_mise_registry_archive(cache_path) == b"registry"
    assert cache_path.exists()
    assert observed_timeout == 10
    assert observed_request is not None
    assert observed_request.get_header("User-agent") == "maniac/0.1"


def test_mise_registry_cold_load_is_single_flight_per_cache_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = _compressed_mise_registry(
        {"registry/ripgrep.toml": b'backends = ["github:BurntSushi/ripgrep"]\n'}
    )
    download_started = threading.Event()
    release_download = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    class Response:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return archive

    def fake_urlopen(request: Request, timeout: int) -> Response:
        nonlocal calls
        with calls_lock:
            calls += 1
        download_started.set()
        assert release_download.wait(timeout=2)
        return Response()

    monkeypatch.setattr(discovery, "urlopen", fake_urlopen)
    cache_path = tmp_path / "mise-registry.tar.zst"
    _load_mise_registry.cache_clear()
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            first = executor.submit(_load_mise_registry, cache_path)
            assert download_started.wait(timeout=2)
            rest = [executor.submit(_load_mise_registry, cache_path) for _ in range(7)]
            release_download.set()
            registries = [first.result(), *(future.result() for future in rest)]

        assert calls == 1
        assert registries == [{"ripgrep": "BurntSushi/ripgrep"}] * 8
        assert not list(tmp_path.glob("*.tmp"))
    finally:
        _load_mise_registry.cache_clear()


def test_query_mise_registry_uses_the_bound_config_cache_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = Config(cache_dir=tmp_path / "cache" / "maniac" / "repos")
    observed: list[Path] = []

    def read_archive(cache_path: Path) -> None:
        observed.append(cache_path)

    monkeypatch.setattr(discovery, "_read_mise_registry_archive", read_archive)
    _load_mise_registry.cache_clear()

    assert discovery._query_mise_registry("ripgrep", config=config) is None
    assert observed == [config.cache_dir.parent / "mise-registry.tar.zst"]

    _load_mise_registry.cache_clear()


def test_which_stops_at_the_first_path_entry(monkeypatch, tmp_path: Path) -> None:
    """ADR-0020/0061 reject falling through to a later entry when the first is
    unclaimed: "unclaimed" cannot distinguish a wrapper from a shadowing
    build. `which` never gets that far -- it returns the first executable
    match regardless of what claims it afterward.
    """
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    (first_dir / "tool").touch(mode=0o755)
    (second_dir / "tool").touch(mode=0o755)
    monkeypatch.setenv("PATH", f"{first_dir}:{second_dir}")

    assert pathcache.which("tool") == first_dir / "tool"


def test_which_skips_a_non_executable_match(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "tool").touch(mode=0o644)
    monkeypatch.setenv("PATH", str(tmp_path))

    assert pathcache.which("tool") is None
