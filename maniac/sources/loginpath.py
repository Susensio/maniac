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
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePath

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
# (venv, conda, uv, direnv, mise), not a guess.
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
# `__MISE_` alongside `MISE_`: `mise activate bash` exports `__MISE_EXE`,
# `__MISE_DIFF` and `__MISE_ORIG_PATH` (the whole pre-activation $PATH)
# under the underscored form, observed on this machine, and a shell hook
# reading any of them re-applies the project's tool versions.
_ACTIVATION_ENV_PREFIXES = ("UV_", "DIRENV_", "MISE_", "__MISE_")

# Activation markers that name their root directly, so a $PATH entry below
# one is attributable to that activation rather than to the machine. Only
# these can drive fallback sanitization -- mise's markers name no per-tool
# root, so its entries stay, since removing them would mean reconstructing
# an install directory by convention instead of by evidence.
_ACTIVATION_ROOT_ENV_KEYS = ("VIRTUAL_ENV", "CONDA_PREFIX")

# XDG_CONFIG_HOME is passed through deliberately, not scrubbed:
# /etc/profile.d/profile_xdg.sh does
# `_confdir=${XDG_CONFIG_HOME:-$HOME/.config}; . "${_confdir}/profile"`, so
# it selects which profile the login shell reads -- the user's real one, on
# a machine where something (a display manager, a container entrypoint, a
# wrapper) sets it ahead of MANIAC rather than the shell deriving it itself.
# Scrubbing it forces the shell onto $HOME/.config's profile even when that
# isn't where the caller's real profile lives, which answers with a $PATH
# the user doesn't have -- and nothing catches it, because the degraded-
# login-path warning only fires when the shell builds no entries at all.
# The isolation this used to buy (a test harness redirecting it to protect
# the real manifest) belonged to the tests, not to this function; they now
# arrange it themselves.
#
# XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS and XDG_CONFIG_DIRS stay
# unscrubbed on purpose: the systemd pull needs the first two to reach the
# user bus, and the third is the system-wide search path, not a per-user
# redirect. Removing one of these to "finish the job" breaks the systemd
# pull with no test failing, because on a correctly configured machine the
# sixth fallback covers it.


def _login_shell_env() -> dict[str, str]:
    """The caller's environment, minus $PATH and every activation marker.

    Everything else (`$HOME`, locale, `$SHELL` itself, `XDG_CONFIG_HOME`)
    passes through unchanged -- only the channels a directory- or venv-
    triggered activation could use to reconstruct a project `$PATH` are
    scrubbed.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in _ACTIVATION_ENV_KEYS
        and not key.startswith(_ACTIVATION_ENV_PREFIXES)
    }
    env["PATH"] = _BOOTSTRAP_PATH + os.pathsep + _LOGIN_PATH_PROBE
    return env


@dataclass(frozen=True, slots=True)
class LoginPath:
    """`$PATH` for lookups, plus whether a login shell actually produced it.

    `degraded` marks a fallback: the caller's own `$PATH` with activation
    entries removed, standing in for an answer about the machine. A caller
    that validates a resolved root (ADR-0029 does this for Mise aliases)
    can refuse a degraded answer instead of treating it as authoritative.
    """

    path: str
    degraded: bool

    @property
    def dirs(self) -> list[Path]:
        """`path` split on `os.pathsep`, empty entries dropped."""
        return [Path(entry) for entry in self.path.split(os.pathsep) if entry]


def _activation_roots() -> list[PurePath]:
    """Roots named by a currently set activation marker."""
    return [
        PurePath(os.path.normpath(value))
        for key in _ACTIVATION_ROOT_ENV_KEYS
        if (value := os.environ.get(key))
    ]


def _degraded_login_path() -> LoginPath:
    """Inherited `$PATH`, minus every entry under a set activation root.

    A fallback still has to answer a question about the machine, so an
    entry the environment itself attributes to an activation goes. An
    entry with no marker behind it stays: without evidence, dropping it
    would be guessing at which directories belong to the machine.
    """
    roots = _activation_roots()
    entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    kept = [
        entry
        for entry in entries
        if not any(
            PurePath(os.path.normpath(entry)).is_relative_to(root) for root in roots
        )
    ]
    return LoginPath(path=os.pathsep.join(kept), degraded=True)


@cache
def login_path() -> LoginPath:
    """The login shell's `$PATH`, stripped, as a `LoginPath`.

    Falls back to `_degraded_login_path()` -- the inherited `$PATH` minus
    every entry under a set activation root -- and logs a warning when
    `$SHELL` is unset, the shell exits non-zero, spawning it raises
    `OSError`, it times out, or it produces empty output. ADR-0020's
    Consequences names a container or CI runner as the case this makes
    unexamined rather than fatal: it degrades instead of crashing, and the
    warning plus `LoginPath.degraded` is what keeps that degradation from
    being silent.
    """
    shell = os.environ.get("SHELL")
    if not shell:
        logger.warning("$SHELL is unset; falling back to the inherited $PATH")
        return _degraded_login_path()

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
        return _degraded_login_path()
    except OSError as e:
        logger.warning(
            "Failed to spawn login shell; falling back to the inherited $PATH",
            shell=shell,
            error=str(e),
        )
        return _degraded_login_path()

    if result.returncode != 0:
        logger.warning(
            "Login shell exited non-zero extracting $PATH; falling back to "
            "the inherited $PATH",
            shell=shell,
            returncode=result.returncode,
        )
        return _degraded_login_path()

    path = (result.stdout or "").strip()
    if not path:
        logger.warning(
            "Login shell produced an empty $PATH; falling back to the inherited $PATH",
            shell=shell,
        )
        return _degraded_login_path()

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
            "to the inherited $PATH, sanitized of entries under a set "
            "activation root",
            shell=shell,
        )
        return _degraded_login_path()

    # The probe is an implementation detail of the check above -- strip it
    # before it can reach a caller.
    entries = [entry for entry in path.split(os.pathsep) if entry.rstrip("/") != probe]
    return LoginPath(path=os.pathsep.join(entries), degraded=False)


def which_login(name: str) -> Path | None:
    """First executable, non-directory match for `name` across `login_path().dirs`.

    Stops at the first hit rather than falling through to a later `$PATH`
    entry when it is unclaimed by any provider -- ADR-0020 rejects that
    fallthrough specifically: "unclaimed" cannot distinguish a transparent
    wrapper from a genuinely different build that shadows a managed one, so
    trying the next entry would attribute one binary's documentation to
    another with no evidence for it.
    """
    for directory in login_path().dirs:
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None
