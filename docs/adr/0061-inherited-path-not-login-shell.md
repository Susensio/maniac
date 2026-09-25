# ADR-0061: Resolve tools through the inherited PATH instead of a login shell's

Status: Accepted
Date: 2026-09-25
Supersedes: [ADR-0020](0020-login-shell-path-refuse-contextual.md), [ADR-0044](0044-xdg-config-home-passthrough.md)

## Context

ADR-0020 took `$PATH` from `$SHELL -lc 'printenv PATH'` run at `$HOME`, so that a project's activated environment would not be mistaken for the machine's.
ADR-0021 made that login `$PATH` the resolution source, and ADR-0044 tuned which variables reached the shell.
The machinery grew to a bootstrap `$PATH`, a probe to detect a shell that built nothing, a scrub list of activation markers, and a degraded fallback that ADR-0060 then ruled out.

On 2026-09-25 two measurements in a throwaway `$HOME` showed the login shell does not describe the machine either.
Debian's stock `.bashrc` returns early in non-interactive shells, so `mise activate` placed there, as Mise documents, never ran, and global Mise tools were invisible.
An interactive login shell (`-ilc`) fixed that in every fixture, but runs whatever the rc file does.
An rc file ending in `exec tmux` produced no `$PATH` at all, and the development machine's own `.bashrc` execs into fish.
Neither variant can reproduce "the user's `$PATH`" without executing, and depending on, the user's shell configuration.

Three shapes were weighed:
the non-interactive login shell plus asking Mise from `$HOME` for its global tools, which remained an approximation that misses anything else set in `.bashrc`;
the interactive login shell, rejected over `exec` in rc files;
and the inherited `$PATH`, with the project case handled where it bites.

What the project case costs was checked against the providers.
Virtualenv, conda, `node_modules/.bin` and direnv entries are claimed by no provider, so a binary resolved there is already refused as unclaimed.
The one provider that can claim a project-scoped binary is Mise, for a version pinned by a project's `mise.toml`.

## Decision

maniac resolves binaries through the `$PATH` it inherited, as it stands.
It describes the environment it is run in, as `man` itself does.
`maniac/sources/loginpath.py` is removed: the login-shell spawn, bootstrap and probe, the activation-marker scrub (so ADR-0044 is superseded with it), and the degraded fallback.
ADR-0021's "resolution source" is now the inherited `$PATH`; the rest of ADR-0021 stands.

A Mise installation that is active only because of a project's configuration is refused, with the reason stated.
The evidence is Mise's own answer for `$HOME`: `mise ls --current` / `mise bin-paths` run with cwd=`$HOME` and every `MISE_*` variable removed (`MISE_CONFIG_FILE` alone was measured to leak a project config into that query).

Carried forward unchanged from ADR-0020:
the first `$PATH` hit is the binary maniac reasons about, and it never falls through to a later occurrence when the first is unclaimed;
an unclaimed binary is refused with the reason given;
synthesis for an unclaimed binary records its `--version` output verbatim and treats any change as `outdated`.

## Consequences

About 280 lines of environment reconstruction and its tests go away, and with them the ~60ms shell spawn and every failure mode of a user's rc file.

`list` and `install` now depend on where they are run.
Inside an activated virtualenv, `install ruff` resolves to the venv copy and is refused as unclaimed even if a global uv tool exists later on `$PATH`, where before it resolved to the global one.
The refusal must name the resolved path so the user can see why.

Launched with a minimal `$PATH` (cron, an IDE, a service), maniac sees only what that `$PATH` reaches.
That is a valid environment, and per ADR-0060 it is described, not compensated for.

Each `list`/`install` touching a Mise tool pays one `mise` call from `$HOME` (~170ms measured), cached per process.
