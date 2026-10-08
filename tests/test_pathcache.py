"""Tests for reading `$PATH` from the login shell (ADR-0062).

These spawn a real `/bin/sh -l` against a throwaway `$HOME` whose `.profile`
the test writes, so what is asserted is what a login shell actually does
with the environment maniac hands it, not a model of it.
"""

import os
import stat
import threading
from pathlib import Path

import pytest

from maniac.exceptions import BrokenLoginShell
from maniac.sources import pathcache

# Bound at import, before conftest's autouse fixture swaps in the test `$PATH`.
from maniac.sources.pathcache import _spawn_login_shell as _real_spawn

pytestmark = pytest.mark.skipif(
    not Path("/bin/sh").exists(), reason="needs a POSIX /bin/sh"
)


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A fresh `$HOME`, `$SHELL=/bin/sh`, and the real login-shell spawn."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setattr(pathcache, "_spawn_login_shell", _real_spawn)
    return home


def _profile(home: Path, body: str) -> None:
    (home / ".profile").write_text(body, encoding="utf-8")


def _script(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_an_activated_venv_is_not_on_the_login_path(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The point of ADR-0062: a venv's `bin/` first on the inherited `$PATH`
    does not survive into the answer, even through a profile that builds
    on the `$PATH` it was given (`PATH="$HOME/global:$PATH"`)."""
    venv_bin = tmp_path / "project" / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    monkeypatch.setenv("VIRTUAL_ENV", str(venv_bin.parent))
    monkeypatch.setenv("PATH", f"{venv_bin}{os.pathsep}{os.environ['PATH']}")
    _profile(home, 'PATH="$HOME/global:$PATH"; export PATH\n')

    dirs = pathcache.path_dirs()

    assert home / "global" in dirs
    assert venv_bin not in dirs


def test_the_shell_is_handed_the_bootstrap_path_not_the_inherited_one(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Independent of any `/etc/profile`: Debian's overwrites `PATH`, so the
    test above would pass there even if the inherited `$PATH` leaked, while
    Arch's appends to whatever it was given. A stand-in shell that echoes
    its own `$PATH` shows what maniac hands over."""
    venv_bin = tmp_path / ".venv" / "bin"
    monkeypatch.setenv("PATH", f"{venv_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv(
        "SHELL", str(_script(tmp_path / "echo-shell", 'printf "%s\\n" "$PATH"\n'))
    )

    assert pathcache.path_dirs() == [
        Path(entry) for entry in pathcache._BOOTSTRAP_PATH.split(os.pathsep)
    ]


def test_activation_markers_are_scrubbed_and_user_config_is_kept(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A profile sees no activation state, but still sees configuration --
    `MISE_CONFIG_DIR` and `UV_TOOL_DIR` are the user's, not an activation's
    (ADR-0061 Corrections measured dropping them)."""
    for key in (
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "CONDA_PREFIX_1",
        "__MISE_DIFF",
        "MISE_SHELL",
        "DIRENV_DIR",
    ):
        monkeypatch.setenv(key, "/somewhere")
    monkeypatch.setenv("MISE_CONFIG_DIR", "/config/mise")
    monkeypatch.setenv("UV_TOOL_DIR", "/tools/uv")
    _profile(
        home,
        "for v in VIRTUAL_ENV CONDA_PREFIX CONDA_PREFIX_1 __MISE_DIFF MISE_SHELL"
        " DIRENV_DIR; do\n"
        '  eval "[ -n \\"\\${$v:-}\\" ]" && PATH="$HOME/leaked-$v:$PATH"\n'
        "done\n"
        '[ -n "${MISE_CONFIG_DIR:-}" ] && PATH="$HOME/kept-mise:$PATH"\n'
        '[ -n "${UV_TOOL_DIR:-}" ] && PATH="$HOME/kept-uv:$PATH"\n'
        "export PATH\n",
    )

    dirs = pathcache.path_dirs()

    assert not [d for d in dirs if d.name.startswith("leaked-")]
    assert home / "kept-mise" in dirs
    assert home / "kept-uv" in dirs


def test_the_login_shell_runs_at_home_not_the_callers_directory(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    _profile(home, 'PATH="$PWD/here:$PATH"; export PATH\n')

    assert home / "here" in pathcache.path_dirs()


def test_the_login_shell_is_not_interactive(home: Path) -> None:
    """ADR-0062: the login profile is the convention for environment; an
    interactive shell would also run rc files, `exec` into fish included."""
    _profile(
        home,
        'case $- in *i*) PATH="$HOME/interactive:$PATH";; '
        '*) PATH="$HOME/login:$PATH";; esac\nexport PATH\n',
    )

    dirs = pathcache.path_dirs()

    assert home / "login" in dirs
    assert home / "interactive" not in dirs


def test_unset_shell_is_reported(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHELL")

    with pytest.raises(BrokenLoginShell, match=r"\$SHELL is unset") as raised:
        pathcache.path_dirs()
    assert raised.value.shell is None


def test_a_shell_that_cannot_start_is_reported(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "no-such-shell"
    monkeypatch.setenv("SHELL", str(missing))

    with pytest.raises(BrokenLoginShell, match="could not be started") as raised:
        pathcache.path_dirs()
    assert raised.value.shell == str(missing)


def test_a_failing_profile_is_reported_with_its_last_stderr_line(home: Path) -> None:
    _profile(home, 'echo "profile: broken line 3" >&2\nexit 3\n')

    with pytest.raises(BrokenLoginShell, match=r"exited 3.*broken line 3"):
        pathcache.path_dirs()


def test_a_shell_that_prints_no_path_is_reported(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SHELL", str(_script(tmp_path / "mute-shell", "exit 0\n")))

    with pytest.raises(BrokenLoginShell, match="printed no"):
        pathcache.path_dirs()


def test_a_hanging_profile_is_reported(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pathcache, "_LOGIN_SHELL_TIMEOUT", 1)
    _profile(home, "sleep 10\n")

    with pytest.raises(BrokenLoginShell, match="timed out after 1s"):
        pathcache.path_dirs()


def test_the_shell_is_spawned_once_per_process_even_from_many_threads(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`list` resolves names from a thread pool; the first lookups racing
    must still spawn one shell, and every later lookup reuse its answer."""
    spawns = tmp_path / "spawns"
    shell = _script(
        tmp_path / "counting-shell",
        f'echo x >> "{spawns}"\nsleep 0.2\necho "$HOME/bin"\n',
    )
    monkeypatch.setenv("SHELL", str(shell))

    results: list[list[Path]] = []
    threads = [
        threading.Thread(target=lambda: results.append(pathcache.path_dirs()))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    pathcache.which("anything")

    assert spawns.read_text(encoding="utf-8").count("x") == 1
    assert results == [[home / "bin"]] * 8


def test_a_failure_is_memoized_but_raised_fresh_each_time(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A broken profile does not heal mid-run, so it is asked once; each
    caller still gets its own exception, never a shared one whose traceback
    grows with every raise."""
    spawns = tmp_path / "spawns"
    shell = _script(tmp_path / "failing-shell", f'echo x >> "{spawns}"\nexit 1\n')
    monkeypatch.setenv("SHELL", str(shell))

    raised = []
    for _ in range(2):
        with pytest.raises(BrokenLoginShell) as info:
            pathcache.path_dirs()
        raised.append(info.value)

    assert spawns.read_text(encoding="utf-8").count("x") == 1
    assert raised[0] is not raised[1]
    assert str(raised[0]) == str(raised[1])
