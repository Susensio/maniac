# Implementation State

[ADR-0020](adr/0020-login-shell-path-refuse-contextual.md) and [ADR-0021](adr/0021-login-path-resolution-version-readback.md) are implemented, verified live, and carry their own reasoning — including the finding that cost the most to reach, that a login shell *inherits* `$PATH` and appends to it rather than constructing one, so it reports the caller's answer unless its environment is scrubbed first.
[ADR-0019](adr/0019-earn-synthesized-page-version.md)'s last loose end is closed: `ty` was regenerated and its manifest entry records `version: "0.0.78"` where it previously recorded none.

## Upstream availability in `list` is in flight

`maniac list fzf` currently reports `missing` after both installed copies of `fzf.1` were deleted, even though it resolves `junegunn/fzf` and the version-matched cached checkout contains `man/man1/fzf.1`.
The cause is bounded: `list` resolves the repository identity but only checks reachable and install-root pages; the repository-manpage probe is called by `install` alone.

[ADR-0018](adr/0018-list-reports-manpage-reachability.md) already requires the missing network half and fixes the four-state output, so no new decision is needed.
The accepted implementation is a cache-first versioned probe, followed by at most eight concurrent network probes, with one final ordered table after every row settles.
Piped output remains bare final names and therefore waits for the same classification.
Only a page accepted by the shared `discover_repo_manpage` primitive becomes `available` with source `upstream`; failures remain `missing` with the repository still visible.

The related repository-link width defect is already fixed by `d48ba37`: Rich now links only the visible target text rather than the padded table cell.

## ADR-0022 implementation is complete; final re-audit remains

[ADR-0022](adr/0022-config-bound-at-construction.md) was implemented in its required order by `1323dc5`, `8351e53`, `42a98b3`, and `51b5fc9`, with review corrections in `0107c7a`, `726cb20`, `e09b7be`, and `65ebafe`.
Configuration and provider availability bind at first `Config` construction, one instance is threaded from the CLI entry point, discovery uses its paths, and the autouse no-real-XDG-write fixture now patches the environment rather than module internals.

The last audit found that construction-time model validation broke ADR-0011's flag-first precedence and that provider availability was still live until first model use.
`65ebafe` fixes both: availability is snapshotted without raising at construction, and only the precedence winner is validated when model resolution is requested.
Its full check passed 412 tests; a fresh independent review and final isolated check still need to confirm the correction before this section can be removed.

Open work with no owner remains in `docs/BACKLOG.md`.

## What changed on the development system on 2026-09-10

Environment state, not code — anyone re-measuring needs to know this happened.

`fzf`'s and `tmux`'s shipped manpages were **deleted** from their mise install roots (`~/.local/share/mise/installs/fzf/0.74.3/fzf.1`, `~/.local/share/mise/installs/tmux/3.7b/tmux.1`), deliberately, to remove tier 1 and force tier 2 to prove itself.
Restore either with `mise install <tool> --force`.

`fzf` was installed by MANIAC through tier 2, then the user deleted both that managed page and the mise install-root page again on 2026-09-10.
Its cached version-matched repository remains at `~/.cache/maniac/repos/fzf@v0.74.3` and contains `man/man1/fzf.1`; this is the live reproducer for the `list` work above.
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
