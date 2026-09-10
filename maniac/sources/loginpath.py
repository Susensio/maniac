"""Cache the login shell's `$PATH`, extracted once per process (ADR-0020).

`discovery.py` needs `$PATH` as a login shell at `$HOME` would see it, not
the `$PATH` MANIAC inherited from whoever invoked it -- but getting there
means spawning a shell and waiting out its startup, ~57ms measured against a
~3.5s run. `enumerate_installations` walks every name on that `$PATH` once,
and `install`/`list` can resolve dozens of binary names in one process, each
of which would otherwise pay that cost again. `@cache` means it is paid once
per process, never per binary; uncached, dozens of lookups at ~57ms each
would be catastrophic where one is free.
"""

import os
import subprocess
from functools import cache
from pathlib import Path

from ..logging import logger

# Guards against a login shell that hangs (a broken rc file blocking on
# stdin, say) rather than the ordinary ~57ms cost, which this never has to
# wait out under normal conditions.
_LOGIN_SHELL_TIMEOUT = 5

# A login shell is supposed to *construct* $PATH from system defaults and
# its own rc files, not receive one already built by the caller -- `uv run`
# prepends `.venv/bin`, and nothing in a typical rc file ever resets it, so
# an inherited $PATH here reintroduces exactly the caller-dependence ADR-0020
# exists to remove. /usr/bin:/bin:/usr/sbin:/sbin is enough for the shell to
# find `printenv` and build from; that value showing up in the result is
# correct, not a bug.
_BOOTSTRAP_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

# Keys and prefixes a login shell's rc files or prompt hooks read to decide
# whether to re-activate a project environment and prepend it back onto
# $PATH -- the same class of re-entry ADR-0020's cwd=$HOME guards against,
# through env instead of cwd. Trimmed here later by someone who doesn't
# know what it defends against is the likely way this regresses, hence the
# comment: each one is a real activation marker observed in the wild
# (venv, conda, uv, direnv), not a guess.
_ACTIVATION_ENV_KEYS = frozenset(
    {
        "VIRTUAL_ENV",
        "VIRTUAL_ENV_PROMPT",
        "CONDA_PREFIX",
        "CONDA_DEFAULT_ENV",
        "CONDA_SHLVL",
        "CONDA_PROMPT_MODIFIER",
    }
)
_ACTIVATION_ENV_PREFIXES = ("UV_", "DIRENV_")


def _login_shell_env() -> dict[str, str]:
    """The caller's environment, minus $PATH and every activation marker.

    Everything else (`$HOME`, locale, `$SHELL` itself) passes through
    unchanged -- only the channels a directory- or venv-triggered
    activation could use to reconstruct a project `$PATH` are scrubbed.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in _ACTIVATION_ENV_KEYS
        and not key.startswith(_ACTIVATION_ENV_PREFIXES)
    }
    env["PATH"] = _BOOTSTRAP_PATH
    return env


@cache
def login_path() -> str:
    """The login shell's `$PATH`, stripped.

    Falls back to the inherited `$PATH` (`os.environ`) and logs a warning
    when `$SHELL` is unset, the shell exits non-zero, spawning it raises
    `OSError`, it times out, or it produces empty output. ADR-0020's
    Consequences names a container or CI runner as the case this makes
    unexamined rather than fatal: it degrades instead of crashing, and the
    warning is what keeps that degradation from being silent.
    """
    shell = os.environ.get("SHELL")
    if not shell:
        logger.warning("$SHELL is unset; falling back to the inherited $PATH")
        return os.environ.get("PATH", "")

    try:
        # `printenv PATH`, not `echo $PATH`: fish prints $PATH space-
        # separated under `echo`, unparseable as the colon-separated list
        # every other shell produces. Fish is installed on the development
        # machine even though $SHELL there is /bin/bash, so a user with
        # $SHELL set to fish is reachable, not theoretical.
        result = subprocess.run(
            [shell, "-lc", "printenv PATH"],
            cwd=Path.home(),
            env=_login_shell_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
            timeout=_LOGIN_SHELL_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "Login shell timed out extracting $PATH; falling back to the "
            "inherited $PATH",
            shell=shell,
        )
        return os.environ.get("PATH", "")
    except OSError as e:
        logger.warning(
            "Failed to spawn login shell; falling back to the inherited $PATH",
            shell=shell,
            error=str(e),
        )
        return os.environ.get("PATH", "")

    if result.returncode != 0:
        logger.warning(
            "Login shell exited non-zero extracting $PATH; falling back to "
            "the inherited $PATH",
            shell=shell,
            returncode=result.returncode,
        )
        return os.environ.get("PATH", "")

    path = (result.stdout or "").strip()
    if not path:
        logger.warning(
            "Login shell produced an empty $PATH; falling back to the inherited $PATH",
            shell=shell,
        )
        return os.environ.get("PATH", "")

    return path


def login_path_dirs() -> list[Path]:
    """`login_path()` split on `os.pathsep`, empty entries dropped."""
    return [Path(entry) for entry in login_path().split(os.pathsep) if entry]


def which_login(name: str) -> Path | None:
    """First executable, non-directory match for `name` across `login_path_dirs()`.

    Stops at the first hit rather than falling through to a later `$PATH`
    entry when it is unclaimed by any provider -- ADR-0020 rejects that
    fallthrough specifically: "unclaimed" cannot distinguish a transparent
    wrapper from a genuinely different build that shadows a managed one, so
    trying the next entry would attribute one binary's documentation to
    another with no evidence for it.
    """
    for directory in login_path_dirs():
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None
