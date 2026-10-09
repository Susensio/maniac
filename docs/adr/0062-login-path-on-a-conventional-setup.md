# ADR-0062: Resolve tools through a non-interactive login shell's PATH, assuming a conventional setup

Status: Accepted; superseded in part by [ADR-0065](0065-a-user-contract-and-a-smaller-command-surface.md) (`install` reports when the invoking shell runs another copy, and `--force` documents that copy for a tool with no global one; the login `$PATH` stands as the default)
Date: 2026-10-08
Supersedes in part: [ADR-0061](0061-inherited-path-not-login-shell.md)

## Context

ADR-0061 dropped the login shell and resolved binaries through the inherited `$PATH`.
Its own Corrections then showed the cost: inside an activated virtualenv, `install ruff` resolves the venv copy, no provider claims it, and tier 3 synthesizes a global, permanent page from a project's binary.
That contradicts the premise every page rests on: a manpage installs globally, so the binary it documents must be the machine's.

ADR-0061 rejected the login shell on two measurements.
Neither is a fault of the login shell itself.

- A non-interactive login shell (`-lc`) missed global Mise tools because the fixture put `mise activate` in `.bashrc`, behind Debian's interactive guard.
  By convention, environment variables belong to the login profile (`.profile`, `.bash_profile`, `.zprofile`), and `.bashrc` is for interactive settings.
  `mise activate` is a prompt hook that switches versions per directory, which is interactive behaviour.
  Mise's own shims page pairs `mise activate --shims` in the login profile (`.bash_profile`, `.zprofile`) with `mise activate` in the rc file.
- An interactive login shell (`-ilc`) ran the whole rc file, and an rc file ending in `exec tmux` or `exec fish` printed no `$PATH`.
  A non-interactive login shell never gets that far: the interactive guard returns first.

The user ruled that maniac assumes a conventional setup: a login profile that builds the environment.
Setups that export `$PATH` only from an interactive rc file are valid (ADR-0060 names them "different but valid"), but supporting them is left as later work.

Separately, `ResolvedTool.executable` was the bare tool name whenever no `bin_dir` was given.
`--help` and `--version` therefore ran through `subprocess`, which searches the inherited `$PATH`.
Under ADR-0020 that meant resolving the global binary and then crawling, and recording the version of, whichever copy the invoking shell put first.
`list`'s version probe for an unclaimed binary had the same flaw.

## Decision

maniac resolves binaries through the `$PATH` printed by `$SHELL -lc 'printenv PATH'`, run once per process with cwd=`$HOME`.
The shell is non-interactive.
It starts from a bootstrap `$PATH` (`/usr/bin:/bin:/usr/sbin:/sbin`), not the inherited one, so a profile that appends to `$PATH` cannot carry a venv's `bin/` through.
Activation state is removed from its environment: `VIRTUAL_ENV`, `VIRTUAL_ENV_PROMPT`, `CONDA_PREFIX`, `CONDA_PREFIX_<n>`, `CONDA_DEFAULT_ENV`, `CONDA_SHLVL`, `CONDA_PROMPT_MODIFIER`, every `__MISE_*`, `MISE_SHELL` and every `DIRENV_*`.
The user's configuration (`MISE_CONFIG_DIR`, `UV_TOOL_DIR`, XDG variables and the rest) passes through unchanged, as ADR-0061's Corrections require.

When the login shell cannot answer, maniac stops (ADR-0060).
`$SHELL` unset, a shell that cannot start, a non-zero exit, a timeout (5s) and an empty answer each raise `BrokenLoginShell` with the reason.
The answer, failure included, is memoized for the process.
`install` and `list` check it before doing any work, so a broken profile is reported once and the command exits 1.
There is no degraded fallback.

Every binary maniac runs is run by its resolved path, never by bare name: the pipeline's `--help` crawl and `--version` probe, and `list`'s version probe for an unclaimed binary.

Carried forward unchanged:
the first `$PATH` hit is the binary maniac reasons about, with no fall-through past an unclaimed one (ADR-0020);
a Mise installation not among Mise's globally selected tools is refused (ADR-0061), including a `mise shell` session pin.

## Consequences

`maniac install ruff` inside an activated venv documents the global ruff again, and venv, conda, direnv and `node_modules/.bin` are all excluded the same way: none of them activate at `$HOME` in a fresh login.

A setup that builds `$PATH` only in an interactive rc file, such as Mise's front-page `mise activate` in `.bashrc`, is not seen.
Its global tools resolve as missing, or to whatever the login profile does reach.
maniac cannot tell this from a machine without those tools, so it is silent here.
Supporting such setups is in `docs/BACKLOG.md`.

A conventional Mise setup puts shims first on the login `$PATH`.
No provider claims a shim yet, so a shimmed tool resolves as unclaimed and falls to tier 3.
Recognising shims is in `docs/BACKLOG.md`.

Each process pays one login-shell spawn (tens of milliseconds), serialized so `list`'s thread pool spawns it once.

Tests resolve through their own `$PATH`: `conftest.py` substitutes the spawn, and `tests/test_pathcache.py` exercises the real one against a throwaway `$HOME`.
