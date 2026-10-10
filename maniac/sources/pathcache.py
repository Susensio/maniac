"""Path resolution shared by every provider, depending on no provider itself.

`_detect_via_registry` (`resolution.py`) tries providers in order until one
claims a binary; for a binary none of them claims -- most of a real `$PATH`
-- every provider runs, each walking the same symlink chain independently.
Memoizing `resolve_cached` here means that walk happens once per unique path
per process, not once per provider.

`resolve_bin_path` lives here rather than beside the registry so that naming
a binary needs nothing from the provider layer: providers and the modules
they import can both reach it without an upward import.

`$PATH` itself is the login shell's, read once per process (ADR-0062): a
manpage installs globally, so the binary it documents is the one a fresh
login at `$HOME` reaches, not whatever an activated project put first on
the `$PATH` maniac happened to inherit.
"""

import os
import secrets
import subprocess
import threading
from functools import cache
from pathlib import Path

from ..exceptions import BrokenLoginShell


@cache
def resolve_cached(path: Path) -> Path:
    """`path.resolve()`, memoized for the life of the process."""
    return path.resolve()


# Guards against a login shell that hangs (an rc file blocking on stdin,
# say). The ordinary cost is tens of milliseconds, paid once per process.
_LOGIN_SHELL_TIMEOUT = 5

# A login shell is meant to *build* `$PATH` from the system profile and the
# user's own, not inherit one: `uv run` and venv activation prepend their
# `bin/`, and a profile that appends (`PATH="$HOME/bin:$PATH"`, Arch's
# `append_path`) would carry that prefix straight through. Starting from
# this is what keeps the answer the machine's. It only has to be enough for
# the shell to start and find `printenv`.
_BOOTSTRAP_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

# What an activated environment exports so its own hooks can re-enter it,
# removed so the spawned shell starts as a fresh login would. Activation
# state only -- never a user's configuration: dropping `MISE_CONFIG_DIR`
# and friends made Mise report no global tools at all (ADR-0061
# Corrections), and `UV_*` is uv's configuration, not an activation (an
# activated uv project shows up as `VIRTUAL_ENV`).
_ACTIVATION_ENV_KEYS = frozenset(
    {
        "VIRTUAL_ENV",
        "VIRTUAL_ENV_PROMPT",
        "CONDA_PREFIX",
        "CONDA_DEFAULT_ENV",
        "CONDA_SHLVL",
        "CONDA_PROMPT_MODIFIER",
        "MISE_SHELL",
    }
)
# `CONDA_PREFIX_<n>` records each stacked conda activation; `__MISE_*` is
# what `mise activate` exports (including the pre-activation `$PATH`);
# `DIRENV_*` is direnv's record of the directory it last loaded.
_ACTIVATION_ENV_PREFIXES = ("CONDA_PREFIX_", "__MISE_", "DIRENV_")


def _login_shell_env() -> dict[str, str]:
    """The caller's environment, minus activation state, on the bootstrap `$PATH`."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in _ACTIVATION_ENV_KEYS
        and not key.startswith(_ACTIVATION_ENV_PREFIXES)
    }
    env["PATH"] = _BOOTSTRAP_PATH
    return env


def _spawn_login_shell() -> str:
    """`$SHELL -lc 'printenv PATH'` at `$HOME`, or `BrokenLoginShell` (ADR-0060).

    Non-interactive on purpose (ADR-0062): environment variables belong to
    the login profile by convention, and an interactive shell would run the
    whole rc file, `exec` into another shell included. `printenv`, not
    `echo $PATH`, because fish prints its list space-separated under `echo`.
    `cwd=$HOME` keeps a directory-triggered activation from re-entering.

    The answer is read only between two markers carrying a per-call nonce:
    a profile may print (a greeting, `fortune`, a motd) before the command
    runs, and an EXIT trap it set prints after, and either would otherwise
    become part of a `$PATH` entry (issue #1). `echo` and `;` mean the same
    in sh, bash, zsh and fish (checked against each).
    """
    shell = os.environ.get("SHELL")
    if not shell:
        raise BrokenLoginShell(None, "$SHELL is unset; set it to your login shell")
    nonce = secrets.token_hex(8)
    begin, end = f"maniac-path-begin-{nonce}", f"maniac-path-end-{nonce}"
    try:
        result = subprocess.run(
            [shell, "-lc", f"echo {begin}; printenv PATH; echo {end}"],
            cwd=Path.home(),
            env=_login_shell_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_LOGIN_SHELL_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise BrokenLoginShell(
            shell,
            f"timed out after {_LOGIN_SHELL_TIMEOUT}s; something in your "
            "login profile is waiting (input, a network mount?)",
        ) from e
    except OSError as e:
        raise BrokenLoginShell(shell, f"could not be started: {e}") from e

    if result.returncode != 0:
        stderr = (result.stderr or "").strip().splitlines()
        detail = f": {stderr[-1]}" if stderr else ""
        raise BrokenLoginShell(
            shell, f"exited {result.returncode} reading $PATH{detail}"
        )
    _, began, after_begin = (result.stdout or "").partition(begin + "\n")
    path, ended, _ = after_begin.partition(end)
    if not (began and ended):
        raise BrokenLoginShell(
            shell,
            "did not run the command it was given; check that your login "
            "profile does not `exit` or `exec` another program",
        )
    path = path.strip()
    if not path:
        raise BrokenLoginShell(
            shell, "printed no $PATH; check that your login profile exports one"
        )
    return path


_login_lock = threading.Lock()


@cache
def _login_path_answer() -> tuple[tuple[Path, ...], BrokenLoginShell | None]:
    """The login `$PATH`'s directories, or why there are none, once per process.

    A failure is memoized too: a broken profile does not heal mid-run, and
    retrying would make every tool of a `list` wait out the timeout again.
    """
    try:
        raw = _spawn_login_shell()
    except BrokenLoginShell as e:
        return (), e
    return tuple(Path(entry) for entry in raw.split(os.pathsep) if entry), None


def clear_login_path() -> None:
    """Forget the memoized login `$PATH` (tests, and nothing else)."""
    with _login_lock:
        _login_path_answer.cache_clear()


def path_dirs() -> list[Path]:
    """Directories on the login shell's `$PATH`, in order (ADR-0062).

    Raises a fresh `BrokenLoginShell` on every call when the shell could
    not answer, so one failure's traceback never accumulates another's.
    """
    # Serialized so a thread pool's first lookups spawn one shell, not one each.
    with _login_lock:
        dirs, failure = _login_path_answer()
    if failure is not None:
        raise BrokenLoginShell(failure.shell, failure.reason)
    return list(dirs)


def which(binary_name: str) -> Path | None:
    """First executable, non-directory match for `binary_name` across `path_dirs()`.

    Stops at the first hit rather than falling through to a later `$PATH`
    entry when it is unclaimed by any provider -- ADR-0020 rejects that
    fallthrough specifically: "unclaimed" cannot distinguish a transparent
    wrapper from a genuinely different build that shadows a managed one, so
    trying the next entry would attribute one binary's documentation to
    another with no evidence for it.

    An entry that cannot be examined (a directory this user may not search)
    is passed over, as a shell does and as `resolution.locate_all`' bulk
    walk already did -- it cannot be the binary that runs, so it is not the
    first hit either.
    """
    return _first_executable(path_dirs(), binary_name)


def which_here(binary_name: str) -> Path | None:
    """`which` over the `$PATH` maniac inherited: what the invoking shell runs.

    Never used to choose what a page documents (that is the login `$PATH`,
    ADR-0062); only to say so when the invoking shell would run another
    copy (CONTRACT.md rule 2).
    """
    inherited = [
        Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry
    ]
    return _first_executable(inherited, binary_name)


def _first_executable(directories: list[Path], binary_name: str) -> Path | None:
    for directory in directories:
        candidate = directory / binary_name
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        except OSError:
            continue
    return None


def resolve_bin_path(binary_name: str, bin_dir: str | Path | None) -> Path | None:
    """Locate a binary's path: an explicit directory first, then the login `$PATH`.

    With no `bin_dir`, resolution is exactly `which` -- nothing else.
    `resolution.locate_all` applies the identical first-`$PATH`-entry-wins
    rule in bulk, by walking `$PATH` itself once for every name rather than
    calling `which` once per name; that walk, not a second notion of "which
    binary a name means", is the only reason the mechanics differ here.

    An explicit `bin_dir` is stated intent -- a directory the caller named,
    not one MANIAC found -- and is checked first regardless of what `$PATH`
    would have resolved to.
    """
    if bin_dir is not None:
        explicit_path = Path(bin_dir) / binary_name
        if explicit_path.exists():
            return explicit_path
    return which(binary_name)
