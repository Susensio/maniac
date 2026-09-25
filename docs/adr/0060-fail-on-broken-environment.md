# ADR-0060: Fail with a clear error on a broken user environment instead of compensating for it

Status: Accepted
Date: 2026-09-25

## Context

ADR-0020 took `$PATH` from a login shell and left the failure mode unexamined ("in a container, a cron job, or a CI runner ... the failure mode there is unexamined").
Code written after it filled that gap with compensation.
`login_path()` fell back to the inherited `$PATH`, minus venv and conda roots, whenever `$SHELL` was unset or the login shell failed, timed out, printed nothing or built no `$PATH` of its own.
The result was marked `degraded` and a warning was logged.

On 2026-09-24/25 the backlog item to also strip Mise-activated entries from that fallback was worked through.
Each step pulled in another one.
Stripping Mise entries also stripped the user's global Mise tools.
Putting those back meant asking `mise bin-paths` from `$HOME`, which in turn needed every `MISE_*` variable scrubbed, because `MISE_CONFIG_FILE` alone was measured to leak a project's config into a `$HOME` query.
All of it served a path that a working desktop never takes.

The user's ruling was that maniac should not handle broken systems at all.

## Decision

maniac assumes a working user environment.
When it finds that environment broken, it stops with an error that names what is broken and how to fix it.
It does not guess, degrade quietly, or reconstruct what a working system would have given.

Two cases are told apart, and only the first is covered by this rule:

- **Broken:** something maniac depends on is present but does not work.
  Examples: a login shell that fails or times out, a malformed file maniac must read, `$SHELL` unset where maniac needs it.
  Behaviour: fail clearly.
- **Different but valid:** another shell, an installer that isn't installed, a relocated XDG directory, an rc file that activates a tool only in interactive shells.
  These are legitimate setups.
  Supporting them is ordinary work, not compensation.
  An installer that is absent is skipped, never treated as an error.

Existing compensation code is removed under this rule rather than extended.
The audit of which code falls on which side, and the removals, follow this record.

## Consequences

Less code and fewer layers: no degraded modes to reason about, and no half-right answers that look like real ones.

maniac no longer runs "somehow" in a container, cron job or CI runner where the login shell does not work.
It says why and exits.
Anyone who wants it there has to make the environment work (set `$SHELL`, fix the rc file), not rely on maniac to guess.

The line between broken and different is a judgement made case by case.
The test to apply: does the user's system work for the user?
A working system that maniac does not understand yet is maniac's problem.
A system that fails for the user too is not.
