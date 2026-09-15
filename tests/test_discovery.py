import io
import subprocess
import tarfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Self
from urllib.request import Request

import pytest
import zstandard

from maniac.config import Config
from maniac.models import Installation
from maniac.sources import discovery, loginpath, resolution
from maniac.sources.discovery import (
    _check_mise_toml,
    _clean_git_url,
    _extract_mise_tool_id,
    _load_mise_registry,
    _parse_mise_registry,
    _read_mise_registry_archive,
    _resolve_from_mise,
)
from maniac.sources.loginpath import LoginPath, login_path, which_login
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


def test_check_mise_toml_tool_alias(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        """
[tool_alias]
gh-cli = "github:cli/cli"
custom = "owner/custom"
""",
        encoding="utf-8",
    )
    assert _check_mise_toml(cfg, "gh-cli", "gh") == "cli/cli"
    assert _check_mise_toml(cfg, "other", "custom") == "owner/custom"
    assert _check_mise_toml(cfg, "unknown", "unknown") is None


def test_check_mise_toml_tools_github_and_cargo(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        """
[tools]
"github:sharkdp/fd" = "latest"
"cargo:https://github.com/BurntSushi/ripgrep" = "latest"
"github:junegunn/fzf" = { filter_bins = ["fzf-tmux"] }
""",
        encoding="utf-8",
    )
    assert _check_mise_toml(cfg, "fd", "fd") == "sharkdp/fd"
    assert _check_mise_toml(cfg, "ripgrep", "rg") == "BurntSushi/ripgrep"
    assert _check_mise_toml(cfg, "unknown", "fzf-tmux") == "junegunn/fzf"
    assert _check_mise_toml(cfg, "fmt", "fmt") is None
    assert _check_mise_toml(cfg, "od", "od") is None
    assert _check_mise_toml(cfg, "unknown", "unknown") is None


def test_check_mise_toml_invalid(tmp_path: Path) -> None:
    cfg = tmp_path / "invalid.toml"
    cfg.write_text("invalid = [toml", encoding="utf-8")
    assert _check_mise_toml(cfg, "foo", "foo") is None

    missing = tmp_path / "nonexistent.toml"
    assert _check_mise_toml(missing, "foo", "foo") is None


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
    """With no explicit `bin_dir`, resolution is the login `$PATH`
    (`which_login`) alone -- a binary sitting where the old default pointed,
    `~/.local/bin`, is not picked up just because it is there.
    """
    fake_home = tmp_path / "home"
    decoy_dir = fake_home / ".local" / "bin"
    decoy_dir.mkdir(parents=True)
    (decoy_dir / "tool").touch()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(loginpath, "which_login", lambda name: None)

    assert discover_repo("tool", config=Config()) is None


def test_discover_repo_does_not_use_the_registry_without_an_installation(
    monkeypatch, tmp_path: Path
) -> None:
    """ADR-0015's ruling on Stage 2's gap: name-only registry matching is gone
    everywhere. A binary with no detected installation stays unresolved even
    where the registry would have matched it by bare name -- this is
    `discover_repo("envsubst")` ceasing to return "a8m/envsubst".
    """
    monkeypatch.setattr(loginpath, "which_login", lambda name: None)
    monkeypatch.setattr(
        discovery,
        "_load_mise_registry",
        lambda: {"envsubst": "a8m/envsubst"},
    )

    assert discover_repo("envsubst", bin_dir=tmp_path, config=Config()) is None


def _fake_installation(binary: str) -> Installation:
    return Installation(
        binary=binary,
        bin_path=Path(f"/bin/{binary}"),
        real_path=Path(f"/bin/{binary}"),
        provider="fake",
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
    monkeypatch.setattr(
        loginpath, "login_path", lambda: LoginPath(path=str(tmp_path), degraded=False)
    )

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
    monkeypatch.setattr(
        loginpath,
        "login_path",
        lambda: LoginPath(path=f"{first_dir}:{second_dir}", degraded=False),
    )

    seen_paths: list[Path] = []

    def fake_detect(bin_path: Path):
        seen_paths.append(bin_path)
        return ("fake-provider", _fake_installation("tool"))

    monkeypatch.setattr(resolution, "_detect_via_registry", fake_detect)

    enumerate_installations()

    assert seen_paths == [first_dir / "tool"]


def test_enumerate_installations_skips_non_executable_files(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / "not_executable").touch(mode=0o644)
    monkeypatch.setattr(
        loginpath, "login_path", lambda: LoginPath(path=str(tmp_path), degraded=False)
    )
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
    monkeypatch.setattr(
        loginpath, "login_path", lambda: LoginPath(path=str(tmp_path), degraded=False)
    )
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
    monkeypatch.setattr(
        loginpath, "login_path", lambda: LoginPath(path=str(tmp_path), degraded=False)
    )
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
    mise_dir = tmp_path / "mise"
    mise_dir.mkdir()
    (mise_dir / "a.toml").write_text("[tools]\nrg = 'latest'\n", encoding="utf-8")
    (mise_dir / "b.toml").write_text(
        "[tool_alias]\nrg = 'github:private/rg'\n", encoding="utf-8"
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


def test_malformed_mise_registry_falls_back_without_error(monkeypatch) -> None:
    archive = _compressed_mise_registry({"registry/broken.toml": b"\xff"})
    monkeypatch.setattr(
        discovery, "_read_mise_registry_archive", lambda cache_path: archive
    )
    _load_mise_registry.cache_clear()

    assert _load_mise_registry(Path("registry.tar.zst")) == {}

    _load_mise_registry.cache_clear()


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


def test_login_path_falls_back_when_shell_is_unset(monkeypatch) -> None:
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setenv("PATH", "/inherited/bin")

    assert login_path() == LoginPath(path="/inherited/bin", degraded=True)


def test_login_path_falls_back_when_the_shell_exits_non_zero(monkeypatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("PATH", "/inherited/bin")
    monkeypatch.setattr(
        loginpath.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(["sh"], 1, stdout="/bad/bin"),
    )

    assert login_path() == LoginPath(path="/inherited/bin", degraded=True)


def test_login_path_falls_back_on_timeout(monkeypatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("PATH", "/inherited/bin")

    def fake_run(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="sh", timeout=5)

    monkeypatch.setattr(loginpath.subprocess, "run", fake_run)

    assert login_path() == LoginPath(path="/inherited/bin", degraded=True)


def test_login_path_parses_a_normal_colon_separated_result(monkeypatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setattr(
        loginpath.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            ["sh"], 0, stdout="/opt/homebrew/bin:/usr/local/bin\n"
        ),
    )

    assert login_path() == LoginPath(
        path="/opt/homebrew/bin:/usr/local/bin", degraded=False
    )
    assert login_path().dirs == [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]


def test_login_path_falls_back_when_the_shell_passes_the_probe_through_untouched(
    monkeypatch,
) -> None:
    """A login shell whose rc files never set $PATH has nothing to build
    from and hands the child's $PATH -- bootstrap plus probe -- straight
    back: zero exit, non-empty output, no information. Treated like the
    other five fallback modes.
    """
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("PATH", "/inherited/bin")
    monkeypatch.setattr(
        loginpath.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            ["sh"],
            0,
            stdout=f"{loginpath._BOOTSTRAP_PATH}:{loginpath._LOGIN_PATH_PROBE}\n",
        ),
    )

    assert login_path() == LoginPath(path="/inherited/bin", degraded=True)


def test_login_path_falls_back_when_the_passthrough_is_reordered_or_has_trailing_slashes(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("PATH", "/inherited/bin")
    monkeypatch.setattr(
        loginpath.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            ["sh"],
            0,
            stdout=f"{loginpath._LOGIN_PATH_PROBE}/:/sbin/:/bin:/usr/sbin:/usr/bin/\n",
        ),
    )

    assert login_path() == LoginPath(path="/inherited/bin", degraded=True)


def test_login_path_keeps_a_prepended_real_directory_and_strips_the_probe(
    monkeypatch,
) -> None:
    """A machine that only prepends ~/.local/bin on top of the bootstrap is
    working correctly -- the common rc pattern (`PATH="$HOME/bin:$PATH"`)
    leaves the probe sitting in the tail even here, so its presence alone
    must not be mistaken for the degenerate case. The probe itself never
    reaches the caller.
    """
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("PATH", "/inherited/bin")
    shell_output = (
        f"/home/tester/.local/bin:{loginpath._BOOTSTRAP_PATH}:"
        f"{loginpath._LOGIN_PATH_PROBE}"
    )
    monkeypatch.setattr(
        loginpath.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            ["sh"], 0, stdout=f"{shell_output}\n"
        ),
    )

    assert login_path() == LoginPath(
        path=f"/home/tester/.local/bin:{loginpath._BOOTSTRAP_PATH}", degraded=False
    )


def test_login_path_treats_a_bootstrap_subset_without_the_probe_as_real(
    monkeypatch,
) -> None:
    """A hardened box's rc might deliberately set PATH=/usr/bin:/bin outright,
    replacing the child's $PATH wholesale rather than prepending to it. The
    probe is gone along with the rest of what it replaced -- that is a real,
    constructed answer, not the shell passing $PATH through untouched, even
    though it undershoots the bootstrap set.
    """
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("PATH", "/inherited/bin")
    monkeypatch.setattr(
        loginpath.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            ["sh"], 0, stdout="/usr/bin:/bin\n"
        ),
    )

    assert login_path() == LoginPath(path="/usr/bin:/bin", degraded=False)


def _trigger_fallback(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """Put `login_path()` on the named fallback branch."""
    monkeypatch.setenv("SHELL", "/bin/sh")
    if mode == "shell_unset":
        monkeypatch.delenv("SHELL", raising=False)
        return
    if mode == "oserror":

        def raise_oserror(*args: object, **kwargs: object) -> None:
            raise OSError("no such shell")

        monkeypatch.setattr(loginpath.subprocess, "run", raise_oserror)
        return
    if mode == "timeout":

        def raise_timeout(*args: object, **kwargs: object) -> None:
            raise subprocess.TimeoutExpired(cmd="sh", timeout=5)

        monkeypatch.setattr(loginpath.subprocess, "run", raise_timeout)
        return
    stdout = {
        "non_zero": "/bad/bin",
        "empty": "\n",
        "probe_only": f"{loginpath._BOOTSTRAP_PATH}:{loginpath._LOGIN_PATH_PROBE}\n",
    }[mode]
    returncode = 1 if mode == "non_zero" else 0
    monkeypatch.setattr(
        loginpath.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(["sh"], returncode, stdout=stdout),
    )


_FALLBACK_MODES = (
    "shell_unset",
    "oserror",
    "timeout",
    "non_zero",
    "empty",
    "probe_only",
)


@pytest.mark.parametrize("mode", _FALLBACK_MODES)
def test_every_fallback_returns_a_sanitized_degraded_path(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Every fallback answers about the machine, not about the caller's
    activation: `uv run` inside a project puts `.venv/bin` on the inherited
    `$PATH`, and handing that back reintroduces exactly the caller-dependence
    ADR-0020 exists to remove.
    """
    monkeypatch.setenv("PATH", "/project/.venv/bin:/usr/bin")
    monkeypatch.setenv("VIRTUAL_ENV", "/project/.venv")
    _trigger_fallback(monkeypatch, mode)

    assert login_path() == LoginPath(path="/usr/bin", degraded=True)


def test_fallback_drops_a_conda_rooted_entry_and_keeps_unmarked_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setenv("CONDA_PREFIX", "/opt/conda/envs/x/")
    monkeypatch.setenv(
        "PATH", "/opt/conda/envs/x/bin:/opt/conda/envs/y/bin:/usr/local/bin"
    )

    assert login_path() == LoginPath(
        path="/opt/conda/envs/y/bin:/usr/local/bin", degraded=True
    )


def test_fallback_keeps_a_venv_looking_entry_with_no_marker_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing in the environment attributes `/project/.venv/bin` to an
    activation once `VIRTUAL_ENV` is unset, and dropping it on the strength
    of its name alone would be guessing at what belongs to the machine.
    """
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.setenv("PATH", "/project/.venv/bin:/usr/bin")

    assert login_path() == LoginPath(path="/project/.venv/bin:/usr/bin", degraded=True)


def test_a_caller_can_tell_a_degraded_result_from_a_real_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The verdict travels with the path, so a root validation (ADR-0029's
    shape) can refuse an answer no login shell produced.
    """
    (tmp_path / "tool").touch(mode=0o755)
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))

    degraded = login_path()

    assert degraded.degraded is True
    assert degraded.dirs == [tmp_path]
    assert which_login("tool") == tmp_path / "tool"

    login_path.cache_clear()
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setattr(
        loginpath.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(["sh"], 0, stdout=f"{tmp_path}\n"),
    )

    assert login_path().degraded is False


def test_login_shell_env_scrubs_mise_activation_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`mise activate bash` exports these; a shell hook reading them
    re-applies the project's tool versions, which is a cwd-sensitive answer
    to a question about the machine.
    """
    monkeypatch.setenv("MISE_SHELL", "bash")
    monkeypatch.setenv("__MISE_EXE", "/usr/bin/mise")
    monkeypatch.setenv("__MISE_ORIG_PATH", "/project/bin:/usr/bin")
    monkeypatch.setenv("__MISE_DIFF", "{}")

    env = loginpath._login_shell_env()

    assert "MISE_SHELL" not in env
    assert "__MISE_EXE" not in env
    assert "__MISE_ORIG_PATH" not in env
    assert "__MISE_DIFF" not in env


def test_login_shell_env_scrubs_activation_markers_and_sets_bootstrap_path(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PATH", "/project/.venv/bin:/usr/bin")
    monkeypatch.setenv("VIRTUAL_ENV", "/project/.venv")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/project/.venv")
    monkeypatch.setenv("DIRENV_DIR", "/project")
    monkeypatch.setenv("CONDA_PREFIX", "/opt/conda/envs/x")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/redirected-config")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setenv("XDG_CONFIG_DIRS", "/etc/xdg")
    monkeypatch.setenv("HOME", "/home/tester")

    env = loginpath._login_shell_env()

    assert env["PATH"] == f"{loginpath._BOOTSTRAP_PATH}:{loginpath._LOGIN_PATH_PROBE}"
    assert "VIRTUAL_ENV" not in env
    assert "UV_PROJECT_ENVIRONMENT" not in env
    assert "DIRENV_DIR" not in env
    assert "CONDA_PREFIX" not in env
    # XDG_CONFIG_HOME decides which profile the login shell reads --
    # redirecting it (a test harness protecting a real manifest, say)
    # points the shell at a profile that doesn't exist, which trips the
    # sixth fallback and hands back the caller's own $PATH. The bus
    # variables must survive scrubbing or the systemd pull breaks.
    assert "XDG_CONFIG_HOME" not in env
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
    assert env["XDG_CONFIG_DIRS"] == "/etc/xdg"
    assert env["HOME"] == "/home/tester"


def test_login_path_does_not_leak_the_callers_path_or_virtualenv(
    monkeypatch, tmp_path: Path
) -> None:
    """Regression: `uv run` prepends `.venv/bin` to the inherited `$PATH`,
    and a login shell spawned with that env unscrubbed reports it right
    back, defeating ADR-0020 in exactly the case it exists for -- nothing
    in a typical rc file ever resets an inherited `$PATH`.

    Runs a real shell against a real (sentinel-poisoned) environment; a
    `subprocess.run` mock cannot catch this class of bug, since the bug is
    in what gets handed to the real subprocess call.
    """
    sentinel_bin = str(tmp_path / "sentinel-bin")
    sentinel_venv = str(tmp_path / "sentinel-venv")
    redirected_xdg_config = str(tmp_path / "redirected-xdg-config")
    # A real rc file, so the real subprocess call below builds a $PATH
    # of its own beyond _BOOTSTRAP_PATH -- otherwise the sixth fallback
    # (login shell taught us nothing) would trigger and this test would
    # observe that fallback's inherited $PATH instead of what scrubbing
    # did to the subprocess's own. The rc file appends, so the probe
    # survives in the tail of the shell's own output; asserting it is
    # gone from the result checks that this test's own real subprocess
    # call isn't quietly falling into the degenerate branch either.
    (tmp_path / ".profile").write_text('PATH="$PATH:/rc-built-bin"\n')
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("PATH", f"{sentinel_bin}:/usr/bin:/bin")
    monkeypatch.setenv("VIRTUAL_ENV", sentinel_venv)
    # A directory that does not exist: on a machine where
    # /etc/profile.d/profile_xdg.sh sources ${XDG_CONFIG_HOME}/profile to
    # pull the real environment, an unscrubbed redirect like this would
    # point the login shell at nothing to source. Not observable via the
    # systemd pull itself here -- the sandbox blocks the user bus -- but
    # the redirected value must not reach the child regardless.
    monkeypatch.setenv("XDG_CONFIG_HOME", redirected_xdg_config)

    result = login_path()

    assert sentinel_bin not in result.path
    assert loginpath._LOGIN_PATH_PROBE not in result.path
    assert redirected_xdg_config not in result.path


def test_login_path_runs_the_login_shell_once_across_many_lookups(monkeypatch) -> None:
    """The caching property is the whole point (ADR-0020): the login shell's
    startup is paid once per process, not once per binary looked up.
    """
    monkeypatch.setenv("SHELL", "/bin/sh")
    calls = 0

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(["sh"], 0, stdout="/usr/bin\n")

    monkeypatch.setattr(loginpath.subprocess, "run", fake_run)

    for _ in range(5):
        login_path()

    assert calls == 1


def test_which_login_stops_at_the_first_path_entry(monkeypatch, tmp_path: Path) -> None:
    """ADR-0020 rejects falling through to a later entry when the first is
    unclaimed: "unclaimed" cannot distinguish a wrapper from a shadowing
    build. `which_login` never gets that far -- it returns the first
    executable match regardless of what claims it afterward.
    """
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    (first_dir / "tool").touch(mode=0o755)
    (second_dir / "tool").touch(mode=0o755)
    monkeypatch.setattr(
        loginpath,
        "login_path",
        lambda: LoginPath(path=f"{first_dir}:{second_dir}", degraded=False),
    )

    assert which_login("tool") == first_dir / "tool"
