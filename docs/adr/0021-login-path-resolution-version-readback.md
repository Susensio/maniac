# ADR-0021: Resolve named tools through the login $PATH and read recorded versions back in list, instead of a predicate-only refusal and a write-only version

Status: Accepted
Date: 2026-09-10

## Context

[ADR-0020](0020-login-shell-path-refuse-contextual.md) decided that `$PATH` comes from a login shell run at `$HOME`, that `install` refuses a binary that `$PATH` cannot reach, and that synthesis for an unclaimed binary records the binary's own `--version` output verbatim.
It was accepted with none of it built.
Implementing it exposed three places where the record did not determine the answer, and where the obvious reading of it produced a system that contradicted its own stated goals.

**Where a named tool is resolved.**
Two code paths read `$PATH`, and ADR-0020 addressed only one.
`enumerate_installations` walked `os.environ["PATH"]` to list every binary — clearly in scope.
But `find_installation`, which resolves a single tool the user named, went through `_resolve_bin_path` and `shutil.which`, and ADR-0020 never said whether that changed.
The implementation notes left in `docs/STATE.md` asserted it did not: the login `$PATH` was to be used in `install` "as a *predicate* rather than as an enumeration source".

Followed literally that produced a contradiction.
`ty` was installed both by mise at 0.0.78 and as this repository's own dev dependency at 0.0.75.
Under a predicate-only reading, `maniac install ty` from inside the repository would resolve through `shutil.which` to `.venv/bin/ty`, observe that `.venv/bin` is on no login-`$PATH` directory, and refuse — while `maniac list`, enumerating the login `$PATH`, would concurrently report `ty` resolving perfectly well to the mise installation.
One tool, two commands, two incompatible answers, in the same directory at the same moment.
ADR-0020's Consequences promise the opposite: that `list` "returns the same answer from any directory... and the answer describes the machine rather than the shell".

**Whether a recorded version is ever read.**
ADR-0020 specifies the write — synthesis records `--version` verbatim, and "any change to that string means `outdated`".
It does not say what performs the comparison, and the code that would have to is `_classify` in `maniac/cli/listing.py`, whose staleness test requires an `Installation`.
An unclaimed binary is precisely the case where there is none.
So the string would have been written on every synthesis and read by nothing, and the row would have stayed `ok` however stale it became — the defect ADR-0020 was correcting, surviving the change intended to correct it.

**Whether a tier flag can buy past the refusal.**
The reachability check was first placed inside `install`'s `if not generate_only` branch, which reads naturally as "before any tier runs" and is not.
`maniac install --generate <tool>` walked past it and synthesized a global, permanent page for a binary only the invoking shell could see.
The refusal was reachable by a flag, which is the same failure as not having it.

## Decision

**The login `$PATH` is a resolution source, not only a predicate.**
`resolve_bin_path` searches the login `$PATH` directly instead of calling `shutil.which`, so `list` and `install` resolve every tool identically and a tool installed both globally and inside a project venv resolves to the global copy from anywhere.

The refusal survives unchanged in force and fires on a simpler condition: a tool found on no login-`$PATH` directory at all.
That is still the case ADR-0020 describes and accepts — "a user who genuinely wants `man ruff` for a project-local tool is refused" — reached by asking where the tool is rather than by resolving it wrongly and rejecting the result.
An explicitly passed `bin_dir` keeps precedence and bypasses the login `$PATH`, because a named directory is stated intent and the point of this record is to stop inferring intent from ambient environment.

The predicate-only reading is discarded and `docs/STATE.md`'s note asserting it is corrected rather than left to be re-derived.

**The refusal is prior to tier selection.**
It sits above the branch that picks a tier, not inside one arm of it.
Whether MANIAC should serve a binary at all is a different question from which tier would answer, and no flag may reorder them.
`resolve_bin_path` also becomes public, since it is now a contract between `discovery` and `orchestration.install` rather than a private helper.

**`list` reads back what synthesis recorded.**
`_classify` compares a recorded version against a freshly run `--version` when the row is MANIAC-owned, carries a recorded version, and has no `Installation`.
Any difference is `outdated`, and the string is not parsed — change is the only question.

Two constraints bound it.
ADR-0018's positive-evidence rule holds: a `--version` that fails, times out, or is unsupported yields no comparison and the row reads `ok`, never `outdated`.
And the cheap conditions short-circuit before the subprocess, so only a MANIAC-owned unclaimed page with a recorded version may spawn one — a handful of rows, not the ~50 unclaimed ones a full walk sees.

Deferring the read side was considered and rejected: it would have shipped a manifest field whose presence implied a staleness check that did not exist.

## Consequences

`maniac list` and `maniac install` can no longer disagree about which binary a name refers to.
That property did not hold before this record and was not guaranteed by ADR-0020 alone.

A tool that exists only inside a project environment is now invisible to every MANIAC command, not merely refused by `install`.
That is broader than the refusal alone: such a tool does not appear in `list` either, so a user looking for it finds nothing rather than a row that explains itself.
The refusal message carries the explanation; a missing row does not.

`maniac list` may now spawn subprocesses, which it did not before.
The count is bounded by MANIAC-owned unclaimed pages rather than by `$PATH` size, so it grows with what the user has installed pages for, not with the machine, and no cap is enforced.

`shutil.which` is no longer used to resolve tools, so MANIAC no longer honours `$PATH` manipulations a caller deliberately applied — an activated venv, a `PATH=... maniac install` prefix, direnv.
This is intended, and it removes an escape hatch that previously worked by accident.
`bin_dir` is the supported way to name a location explicitly.

The manifest's `version` field now holds one of three kinds of value: a provider's version, a tag-matched release version ([ADR-0019](0019-earn-synthesized-page-version.md)), or a raw `--version` string.
ADR-0020 noted the first two becoming incomparable in principle; the read side added here means the comparison is now actually performed on the third, so an entry whose tool later gains a provider compares a raw string against a provider version exactly once and reads `outdated` on that transition alone.
The page is regenerated, the entry rewritten in the new form, and it settles — one spurious `outdated`, not worth a migration to avoid.

### What the login shell actually does, which is not what ADR-0020 assumed

ADR-0020 treats `$SHELL -lc 'printenv PATH'` as *obtaining* the machine's `$PATH`.
It does not.
A login shell **inherits** `$PATH` and its rc files append to whatever they were handed; it constructs one only if something in its startup explicitly sets it.
Asking a login shell without scrubbing the environment first therefore returns the caller's `$PATH` — the exact value ADR-0020 exists to stop trusting — and it looks like it worked, because the answer is usually right for the wrong reason.
`uv run` prepending `.venv/bin` is enough to make it wrong, which is the case ADR-0020 was written about.

So the environment passed to that shell is load-bearing.
`_login_shell_env` replaces `PATH` with a bootstrap value and strips activation markers (`VIRTUAL_ENV`, `CONDA_*`, `UV_*`, `DIRENV_*`) — the same re-entry ADR-0020's `cwd=$HOME` guards against, arriving through environment instead of working directory.

That scrub has a cost ADR-0020 did not anticipate.
A machine whose rc files never set `$PATH` — because the base comes from PAM at login and personal entries come from somewhere a shell does not read — returns the bootstrap straight back.
Zero exit, non-empty output, no information.
Encountered live on the development system, this produced zero rows from `list` and a confident refusal of `ruff`, a genuinely global tool, with nothing warning that anything had gone wrong.
That silence is the failure ADR-0018 exists to end, so a login shell contributing no directory beyond the bootstrap is now treated as a sixth failure mode: warn, and fall back to the inherited `$PATH`, consistent with the other five.

The underlying machine configuration was a real gap and was fixed outside this repository, so the path is no longer reachable here and only its tests exercise it.
It is recorded because the assumption that a login shell reports the machine's `$PATH` is natural, wrong, and expensive to rediscover.
