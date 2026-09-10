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

# Appended to the child's $PATH, after _BOOTSTRAP_PATH so it cannot shadow a
# real binary the shell needs during its own startup. Its fate distinguishes
# "the shell passed $PATH through untouched" from "the shell built one" --
# see the comment at its use in login_path() for why presence alone isn't
# the test. Absolute and unresolvable, so it cannot collide with a real
# directory and is recognisable in a $PATH dump if it ever leaks.
_LOGIN_PATH_PROBE = "/nonexistent/maniac-login-path-probe"

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

# XDG_CONFIG_HOME decides which profile the login shell reads --
# /etc/profile.d/profile_xdg.sh does
# `_profile=${XDG_CONFIG_HOME:-$HOME/.config}/profile` and sources it, and
# that profile is what pulls the real environment from systemd. A caller
# that redirects XDG_CONFIG_HOME (a test harness protecting a real
# manifest, say) points the login shell at a profile that doesn't exist,
# so it finds nothing to source and the sixth fallback (login_path())
# quietly hands back the caller's own inherited $PATH instead. Scrubbing
# it is also the more correct answer, not merely the more isolated one: in
# a real login sequence the shell doesn't receive XDG_CONFIG_HOME, it
# *sets* it from environment.d, so letting the shell fall through to its
# own $HOME/.config default matches what a real login does. Same reasoning
# as VIRTUAL_ENV -- a channel the caller's context could use to answer a
# question that's supposed to be about the machine.
#
# XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS and XDG_CONFIG_DIRS stay
# unscrubbed on purpose: the systemd pull needs the first two to reach the
# user bus, and the third is the system-wide search path, not a per-user
# redirect. Removing one of these to "finish the job" breaks the systemd
# pull with no test failing, because on a correctly configured machine the
# sixth fallback quietly covers it.
_XDG_ENV_KEYS = frozenset({"XDG_CONFIG_HOME"})


def _login_shell_env() -> dict[str, str]:
    """The caller's environment, minus $PATH and every activation marker.

    Everything else (`$HOME`, locale, `$SHELL` itself) passes through
    unchanged -- only the channels a directory- or venv-triggered
    activation could use to reconstruct a project `$PATH`, plus
    `XDG_CONFIG_HOME` (which decides which profile the shell reads), are
    scrubbed.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in _ACTIVATION_ENV_KEYS | _XDG_ENV_KEYS
        and not key.startswith(_ACTIVATION_ENV_PREFIXES)
    }
    env["PATH"] = _BOOTSTRAP_PATH + os.pathsep + _LOGIN_PATH_PROBE
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

    # A login shell inherits $PATH rather than constructing it -- scrubbing
    # to _BOOTSTRAP_PATH is what forces it to prove it can build one. The
    # question isn't "does the result contain anything outside the
    # bootstrap" -- a hardened box's rc might deliberately set
    # PATH=/usr/bin:/bin, a real answer that happens to undershoot the
    # bootstrap, and discarding it in favour of the caller's inherited
    # $PATH would reintroduce exactly what this module exists to exclude.
    # The question is whether the shell touched $PATH at all, which is what
    # _LOGIN_PATH_PROBE (appended to the child's $PATH before spawning)
    # answers: it survives only when the shell handed $PATH back untouched.
    # But survival alone isn't the test either -- the common rc pattern
    # prepends (`PATH="$HOME/bin:$PATH"`), which leaves the probe sitting
    # in the tail on a perfectly healthy machine. Degenerate requires both:
    # the probe still present, and nothing besides the probe added to the
    # bootstrap set.
    bootstrap_entries = {
        entry.rstrip("/") for entry in _BOOTSTRAP_PATH.split(os.pathsep) if entry
    }
    probe = _LOGIN_PATH_PROBE.rstrip("/")
    result_entries = {entry.rstrip("/") for entry in path.split(os.pathsep) if entry}
    if probe in result_entries and result_entries <= bootstrap_entries | {probe}:
        logger.warning(
            "Login shell produced no $PATH entries of its own; falling back "
            "to the inherited $PATH, which may include environment-local "
            "directories",
            shell=shell,
        )
        return os.environ.get("PATH", "")

    # The probe is an implementation detail of the check above -- strip it
    # before it can reach a caller.
    entries = [entry for entry in path.split(os.pathsep) if entry.rstrip("/") != probe]
    return os.pathsep.join(entries)


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
