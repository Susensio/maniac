# ADR-0044: Pass XDG_CONFIG_HOME through to the login shell instead of scrubbing it as caller context

Status: Accepted
Date: 2026-09-15

## Context

ADR-0020 made `login_path()` answer a question about the machine rather than about the
shell that invoked MANIAC. `_login_shell_env()` implemented that by scrubbing `$PATH` down
to `_BOOTSTRAP_PATH` and removing every activation marker — `VIRTUAL_ENV`, `CONDA_*`,
`UV_*`, `DIRENV_*`, and later `MISE_*`/`__MISE_*` — so the login shell had to construct a
`$PATH` rather than receive one.

`XDG_CONFIG_HOME` was placed in that same scrub set. It is not an activation marker: no rc
hook reconstructs a `$PATH` from it. Its only effect is selecting which profile the login
shell sources. On the development machine `/etc/profile.d/profile_xdg.sh` held
`_confdir=${XDG_CONFIG_HOME:-$HOME/.config}` followed by `. "${_confdir}/profile"`, so the
variable chose between the user's real profile and `$HOME/.config/profile`.

Two reasons were recorded for scrubbing it. The first was isolation: the code comment named
its beneficiary outright as "a test harness protecting a real manifest", and six test files
did redirect `XDG_CONFIG_HOME` to a temporary directory. The second was a claim that
scrubbing was independently more correct, because a real login *sets* `XDG_CONFIG_HOME`
from `environment.d` rather than receiving it, so falling through to `$HOME/.config`
reproduced what a real login does.

The second reason described one topology as though it were universal. Where a display
manager, a container entrypoint, or a wrapper established the variable ahead of MANIAC, the
login shell would not re-establish it, and scrubbing pointed the probe at a profile the user
did not use. Nothing detected that: the degraded-login-path warning fires only when the
shell builds no entries at all, and a shell that sourced the wrong profile still produces a
normal-looking `$PATH`.

An independent review argued the opposite side and was weighed rather than dismissed. It
held that a per-invocation redirect — a wrapper, or a directory-local environment — would
now choose the spawned shell's profile, returning MANIAC to the caller-dependence ADR-0020
exists to remove, and that the login-session value should be recovered by some other
mechanism instead. That objection is grounded: ADR-0020's Consequences commit to `list`
returning the same answer from any directory, and that record accepted real usability losses
to hold the line, refusing a project-local `man ruff` as "weighed and accepted". A `.envrc`
that exports `XDG_CONFIG_HOME` is a genuine directory-dependent case, and scrubbing
`DIRENV_*` does not catch it because direnv exports the variable itself.

The decisive observation was that both directions can violate ADR-0020's invariant and the
process cannot tell which case it is in. A value inherited from a machine-wide login session
and a value injected for one invocation are indistinguishable from inside MANIAC. Scrubbing
is therefore not the conservative default it appeared to be; it trades a wrong answer for a
relocated configuration against a wrong answer for an injected one. No mechanism was found
that recovers the true login-session value: querying `systemctl --user show-environment`
was considered and rejected, since it reintroduces a systemd dependency on the exact
topology this record declines to assume, and it is unavailable in the containers and CI
runners ADR-0020 already names as unexamined.

## Decision

`XDG_CONFIG_HOME` is passed through to the login shell unchanged. `_XDG_ENV_KEYS` is deleted
rather than emptied.

Activation-marker scrubbing is unchanged and is not covered by this record. Those variables
remain scrubbed because an rc hook can reconstruct a project `$PATH` from the marker alone,
which defeats the `_BOOTSTRAP_PATH` scrub; `XDG_CONFIG_HOME` has no such property, and the
two were only ever adjacent in one `frozenset`, never equivalent.

Isolation from a redirected `XDG_CONFIG_HOME` is the test suite's responsibility. Tests
whose subject is not the login-PATH probe state that dependency explicitly instead of
relying on production code to scrub their harness for them.

ADR-0020 is narrowed, not superseded: taking `$PATH` from a login shell and refusing
binaries it cannot reach still stands, and so does treating the invoking shell's `$PATH` and
activation state as untrustworthy.

## Consequences

A user whose `XDG_CONFIG_HOME` is established outside the login-shell chain now gets a
`$PATH` derived from their real profile, which they did not before.

A caller that injects `XDG_CONFIG_HOME` for a single invocation now steers which profile the
probe reads. Where that directory has no profile the shell sources nothing and
`login_path()` degrades to the inherited `$PATH`, degraded-flagged and activation-sanitized;
where it has one, project-local entries can enter the answer. This is the cost of the
decision and was accepted rather than overlooked. It is the objection recorded above, and it
is the case to re-open this record on if it is ever observed in practice rather than
reasoned about.

MANIAC cannot distinguish the two situations, so neither behaviour can be made conditional.
Any future attempt to detect an injected redirect needs evidence the process does not
currently have, and guessing from the value's shape — comparing it against `$HOME/.config`,
say — would reconstruct intent by convention rather than by evidence, which ADR-0040's
reasoning about install roots already rejects for the same reason.

Tests that redirect `XDG_CONFIG_HOME` now carry their own isolation, so what each one
depends on is visible at the test rather than implied by a scrub two modules away. The cost
is that a future test can reintroduce the coupling silently by redirecting the variable
without stubbing the probe.

The sandbox in which this change was verified had no `~/.profile`, no `~/.bashrc`, and no
reachable session bus, so the login shell built no `$PATH` of its own there and the
pass-through could not be observed end to end on that machine. The unit coverage pins the
environment handed to the child; the profile-selection behaviour it enables was reasoned
from `/etc/profile.d/profile_xdg.sh`, which was read, not exercised.
