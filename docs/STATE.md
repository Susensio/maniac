# Implementation State

[ADR-0020](adr/0020-login-shell-path-refuse-contextual.md) and [ADR-0021](adr/0021-login-path-resolution-version-readback.md) are implemented, verified live, and carry their own reasoning — including the finding that cost the most to reach, that a login shell *inherits* `$PATH` and appends to it rather than constructing one, so it reports the caller's answer unless its environment is scrubbed first.
[ADR-0019](adr/0019-earn-synthesized-page-version.md)'s last loose end is closed: `ty` was regenerated and its manifest entry records `version: "0.0.78"` where it previously recorded none.

## ADR-0022 is accepted and not implemented at all

[ADR-0022](adr/0022-config-bound-at-construction.md) was accepted on 2026-09-10 (`84d8064`) and **no code has been written against it**.
Two attempts were dispatched on the same day and neither committed anything: the first died to a rate limit, the second was stopped to conserve quota partway through reading `_resolve_from_mise`'s caller chain.
The working tree was clean at both deaths, so there is nothing half-done to find or discard — start from the ADR.

It moves configuration binding from module import to first `Config` construction, keeping read-once semantics.
The distinction it draws between "read once per process" and "read at import" is the whole point of the record and is not to be collapsed back: the first is what a short-lived CLI wants, the second is only how that had been implemented and is the one moment the program cannot intervene in.

Four steps, in a required order:

1. `maniac/config.py` — resolve XDG inside `Config`'s field factories instead of the module-level `_XDG_*` constants (l.20–23); move the `config.toml` read (`_CONFIG_VALUES`, l.73) and the packaged limits/timeouts (l.38–41) off import too. The limits/timeouts are currently *class-body* defaults, so they need factories as well. `resolve_model`'s `MANIAC_MODEL` lookup binds at construction rather than per call.
2. Entry points — `cli/__init__.py`'s module-level `default_cfg = Config()` becomes lazy; the CLI constructs one `Config` at entry and threads it.
3. `maniac/sources/discovery.py:208,287` — take XDG from the threaded `Config`.
4. `tests/conftest.py` — the autouse `_no_real_xdg_writes` fixture converts to ordinary env-var patching, **keeping its protection**; the leak it guards must not come back.

Step 3 before step 1 reintroduces the exact bug the ADR exists to remove.
Each step commits separately and green, so a bisect can land between them.

The step-2 investigation got as far as one useful observation worth not re-deriving: `_resolve_from_mise` and `_mise_registry_cache_path` are the two call sites needing a threaded `Config`, and their caller chains are what determines how far the threading has to reach.

Open work with no owner is in `docs/BACKLOG.md`.

## What changed on the development system on 2026-09-10

Environment state, not code — anyone re-measuring needs to know this happened.

`fzf`'s and `tmux`'s shipped manpages were **deleted** from their mise install roots (`~/.local/share/mise/installs/fzf/0.74.3/fzf.1`, `~/.local/share/mise/installs/tmux/3.7b/tmux.1`), deliberately, to remove tier 1 and force tier 2 to prove itself.
Restore either with `mise install <tool> --force`.

`fzf` was then installed by MANIAC through tier 2 and is a **MANIAC-managed page now**: `~/.local/share/man/man1/fzf.1`, manifest `tier: repository`, `source: junegunn/fzf`, `version: 0.74.3`.
`tmux` is currently left with **no manpage at all** — its shipped page is gone and its install failed — which makes it a live reproducer for the `tmux/tmux-builds` mis-resolution and the findings below.

## Two findings from that run that outrank the backlog's ordering

Both are recorded in full in `docs/BACKLOG.md`; they are named here because they bear on whether `install` can currently be trusted.

`get_help` (`sources/crawler.py:189`, marked with a `BUG:` on the line) returns empty stdout as though it were help text, and never reads stderr.
Measured: `tmux --help` writes 0 bytes to stdout, 157 to stderr, and exits 1.

Consequently tier 3 for `tmux` would have synthesized from an empty help string plus `tmux-builds`' README, which documents Docker build scripts and never describes tmux — every line of the resulting page coming from training memory, with no installation-derived evidence behind it, which inverts ADR-0008.
It did not ship only because Gemini returned a 503.
**A floor on synthesis inputs is the cheapest guard and the only one of the recorded items that would have stopped this**, independent of ever fixing upstream repo resolution.

## Baseline, for anyone comparing figures

Measured on the development system after ADR-0020 landed, at 398 tests green (last confirmed at `677442c`):

- `maniac list` returns 76 rows, identical from this repository and from `$HOME`.
- MANIAC-managed pages were `aichat` and `ty`, both carrying a recorded version; `fzf` joined them on 2026-09-10 as the first page earned through tier 2 rather than synthesis.
- One login-shell spawn per process — 61ms cold, 0.003ms cached.

**Figures recorded before ADR-0020 are not comparable to these.** They were taken through `uv run` with `.venv/bin` shadowing real binaries, which is exactly the distortion that work removed; the row count moved from 70 to 76 for that reason alone. Re-measure rather than trusting an older number.
