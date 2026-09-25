# ADR-0020: Take $PATH from a login shell and refuse binaries it cannot reach, instead of trusting the invoking shell

Status: Superseded by [ADR-0061](0061-inherited-path-not-login-shell.md)
Date: 2026-09-09

## Context

`discovery.enumerate_installations` walked the `$PATH` MANIAC inherited from whoever invoked it.
That is a property of the calling shell, not of the machine, and [ADR-0018](0018-list-reports-manpage-reachability.md) had already made `list` report reachability without saying reachability *from where*.

Run through `uv run` inside MANIAC's own repository, `.venv/bin` preceded everything on `$PATH`, so the project's own dev-dependency copies of `ty`, `ruff` and `pytest` sat at the front.
None was claimed by any provider: all eight require a global installation marker — `~/.local/share/mise/installs/`, `~/.local/share/uv/tools/`, `$CARGO_HOME/bin`, `<prefix>/lib/node_modules/`, `$PIPX_HOME/venvs/`, `/Cellar/`, `$GOBIN`, `~/.local/lib` — and a project virtualenv matches none of them.
All three were therefore dropped silently.
The behaviour was correct and arrived by accident: enforced in eight independent places and written down in none.

Commit `94e9410` made `$PATH` precedence the single binary-resolution rule, removing an implicit `~/.local/bin` preference that had been present since the first version of `discover_repo` and was justified by no record.
That removal was right — the hardcode encoded one machine's layout as a global rule — but it made the dependence on the invoking shell total.
`maniac list` at `$HOME` and inside a project legitimately disagreed, with nothing in the interface admitting it.

Two costs were measured on the development system rather than assumed.
`ty` resolved to the repository's own 0.0.75 dev dependency rather than the mise-installed 0.0.78 that the existing page documented.
And because an unclaimed binary yields no `Installation`, regenerating that page would have recorded no version at all, leaving it permanently `ok` under [ADR-0019](0019-earn-synthesized-page-version.md) no matter how stale it became.

A false lead was followed and abandoned before deciding.
Tier 3 synthesises from `--help` alone when no provider claims a binary — `find_subcommands` runs unconditionally on the executable and repository documentation is merely skipped when no source resolves.
So "unclaimed" never meant "cannot generate", and the reading that `missing` was a false invitation to synthesise was wrong.
The question was never capability. It was whether a global, permanent page should document a binary that exists inside one checkout.

That framing is what settles it.
A page installs into a global manpath and persists; a binary reachable only from an activated environment does not.
They are different kinds of thing, and the mismatch is not repaired by generating the page more carefully.

A wrapper script — the shape used by `~/bin/overrides` on the development system, which strips its own directory from `$PATH` and `exec`s the underlying binary — is a distinct case that looks similar.
It is genuinely globally reachable, so the environment question does not reach it, but it matches no provider marker and therefore loses tiers 1 and 2 while tier 3 still works, because `exec` passes `--help` and `--version` through to the real tool.

## Decision

`$PATH` is taken from a login shell run at `$HOME`, not from the environment MANIAC inherited.

Extraction is `$SHELL -lc 'printenv PATH'`.
`printenv` rather than `echo $PATH`: in fish the latter prints the list space-separated, and fish is installed on the development system even though `$SHELL` there is `/bin/bash`, so the difference is reachable rather than theoretical.
Running at `$HOME` rather than the current directory keeps a directory-triggered activation — direnv, an auto-venv hook — from re-entering through the shell that was spawned to escape it.
Measured cost on the development system is 57ms against a ~3.5s run.

Reachability from that `$PATH` is the test for whether MANIAC serves a binary at all.
`install` refuses one that cannot be reached from it, and says why rather than declining quietly.
The refusal names the reason — the binary is reachable only from the current environment, and the page would be global and permanent — because a silent refusal reproduces the failure ADR-0018 was written to correct.

A deny-list of `.venv`, `node_modules`, `.direnv` and their successors was rejected.
It is a growing catalogue of other people's conventions and is wrong by omission the moment a new tool appears, where login-shell reachability tests the property actually cared about and needs no maintenance.

Synthesis for a binary no provider claims records that binary's own `--version` output verbatim, and any change to that string means `outdated`.
The string is not parsed: change is the only question being asked, so no format has to be recognised.
This is consistent with ADR-0019 rather than an exception to it — a help-only page documents exactly the binary that was crawled, so that binary's own version report is matched evidence, more directly than a tag match is.

MANIAC does not look further down `$PATH` when the first hit is unclaimed.
This was considered specifically to recover wrapped binaries and rejected: "unclaimed" does not distinguish a transparent wrapper from a genuinely different build that shadows a managed one, so falling through would attribute one binary's documentation to another with no evidence — the failure ADR-0008 and ADR-0015 exist to prevent, of the same kind as the `fmt` → `nushell/nufmt` collision.
A wrapped tool is reported as unclaimed with the reason given, and loses tiers 1 and 2.

## Consequences

`maniac list` returns the same answer from any directory, which it did not before, and the answer describes the machine rather than the shell.

A tool pinned to a different version by a project's mise configuration is no longer reflected while working in that project.
This follows directly from the decision and is not a defect: the page being reasoned about is global, so the global resolution is the relevant one.

Every run pays a login shell's startup. 57ms was measured on one machine with one shell; a heavier shell configuration pays more, and the cost is unavoidable rather than cached, since caching it would reintroduce staleness of exactly the kind this record is about.

MANIAC now depends on `$SHELL` being set and on a login shell producing a usable `$PATH`. In a container, a cron job, or a CI runner, that assumption is weaker than on a developer's machine, and the failure mode there is unexamined.

A user who genuinely wants `man ruff` for a project-local tool is refused. That case was weighed and accepted: the refusal explains itself, and the alternative was a permanent global page documenting one checkout's copy with no recorded version and no way to ever show it stale.

Wrapping a binary silently downgrades it from an authoritative upstream manpage to synthesis. The wrapper remains fully functional and tier 3 still produces a good page, so nothing breaks — but the tool quietly costs an LLM call it did not need to.
Comparing `--version` output between the first `$PATH` hit and the next was identified as a real evidence-based way to tell a transparent wrapper from a different build, and left unbuilt; `docs/BACKLOG.md` carries it.

Recording a raw `--version` string makes two entries in the manifest incomparable in principle — one holding a provider's version, another holding whatever a binary prints — so a tool that later gains a provider changes which kind of value it stores. Each entry stays self-consistent, since it is compared only against a value derived the same way, but the field no longer holds one kind of thing.
