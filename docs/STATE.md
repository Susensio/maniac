# Implementation State

## ADR-0020 and ADR-0021 landed; one page still needs regenerating

[ADR-0020](adr/0020-login-shell-path-refuse-contextual.md) is implemented in full, and [ADR-0021](adr/0021-login-path-resolution-version-readback.md) records the three things it left underdetermined and how each was settled.
Read ADR-0021 before changing any of this; it carries the reasoning, and this section is only what exists.

### What landed

`$PATH` comes from a login shell run at `$HOME`, via `maniac/sources/loginpath.py`.
`login_path()` is `@cache`d, so the shell's startup is paid once per process rather than once per binary lookup.
`login_path_dirs()` splits it; `which_login()` returns the first executable match and stops there, per ADR-0020's rejection of `$PATH` fall-through.

Both resolution paths in `discovery.py` go through it: `enumerate_installations` walks it, and `resolve_bin_path` searches it instead of calling `shutil.which`.
That second one is ADR-0021's decision and reverses what an earlier draft of this file asserted — `list` and `install` now resolve every tool identically, where a predicate-only reading would have had them disagree about `ty`.
`resolve_bin_path` is public for that reason: it is a contract between `discovery` and `orchestration.install`, not a private helper.

`run_install` refuses a binary the login `$PATH` cannot reach, raising `InstallRefused` (a `ManiacError` subclass) with a message naming why.
The check sits **above** the `generate_only` branch, not inside it — placed inside, `--generate` walked straight past it.
`cli/install.py` catches `InstallRefused` before the generic handler and renders it in yellow without counting a failure, since nothing was attempted.

Tier-3 synthesis for a binary no provider claims now records that binary's own `--version` output verbatim, via `crawler.get_version` (which returns `None` on every failure mode rather than raising).
`crawler._run_cli_flag` holds the subprocess machinery `get_help` and `get_version` share.
`listing._classify` reads it back: when a row is owned, carries a recorded version, and has no `Installation`, it re-runs `--version` and compares. The cheap checks short-circuit first, so only MANIAC-owned unclaimed pages can spawn a subprocess.

### The environment scrub, and why it is not optional

A login shell **inherits** `$PATH` and its rc files append to what they were handed.
Asked without scrubbing, it returns the caller's `$PATH` — including `uv run`'s `.venv/bin` — and looks like it worked, because the answer is usually right for the wrong reason.
`_login_shell_env` therefore replaces `PATH` with a bootstrap value and strips activation markers (`VIRTUAL_ENV`, `CONDA_*`, `UV_*`, `DIRENV_*`), the same re-entry `cwd=$HOME` guards against arriving through a different channel.

That scrub has a failure mode of its own: a machine whose rc files never set `$PATH` returns the bootstrap straight back — zero exit, non-empty output, no information.
It is treated as a sixth fallback alongside `$SHELL` unset, non-zero exit, timeout, `OSError` and empty output: warn, and use the inherited `$PATH`.
Detection is a conjunction — the probe sentinel survived **and** nothing outside the bootstrap was added — because an rc file that deliberately sets a small `$PATH` has constructed a real answer and must not be discarded.

This was found live, not reasoned about: on the development system it produced zero rows from `list` and a confident refusal of `ruff`, a genuinely global tool, with nothing warning that anything was wrong.
The underlying cause was a gap in the user's own shell configuration, fixed outside this repository — a non-interactive login shell in a graphical session had no route to `environment.d`.
So this path is no longer reachable here and only its tests exercise it.

### Verified

`just check` green.
Full live verification is pending re-run after the sentinel change; the previous full run was 394 tests with the four `just check` stages clean, and confirmed: exactly one login-shell spawn per process (`strace`), 61ms cold against 0.003ms cached, and the `--generate` refusal firing before any tier.

The measurement caveat that governed every earlier figure in this file is **gone**: numbers no longer have to be taken through `uv run` with `.venv/bin` shadowing real binaries, because that is precisely what this work removes.
Re-measure anything quoted from before ADR-0020 rather than trusting it.

### Unfinished

**`ty` was never regenerated.**
Carried over from [ADR-0019](adr/0019-earn-synthesized-page-version.md): four attempts returned `litellm.ServiceUnavailableError` (Gemini 503), so its manifest entry still reads `version: null` and it cannot show `outdated`.
Nothing is broken; the work did not complete.

Re-run plain `maniac install ty` when the API recovers — unflagged, not `--generate`: ADR-0016 orders the tiers authoritative-first and tiers 1 and 2 record a version too, so forcing synthesis can only buy a worse page for an LLM call it did not need.
For `ty` specifically it makes no difference — `--no-generate` reported no install-root or repository page — but the habit matters.

Worth knowing: `ty` now resolves differently than when that attempt was made.
It previously resolved to this repository's 0.0.75 dev dependency and was unclaimed; it now resolves to the mise-installed 0.0.78 and is claimed by the mise provider, so a regeneration will record a real version rather than none.
