# State: the contract redesign

Committed to on 2026-10-09 ([ADR-0065](adr/0065-a-user-contract-and-a-smaller-command-surface.md)).
Target behaviour: [`docs/CONTRACT.md`](CONTRACT.md).
This file is the working progress report; it is deleted when the last phase lands.

Every phase leaves `just check` green and is pushed when done.
When a phase lands, it updates the README for what became live and marks the ADRs it overrides as superseded in part.

## Phases

### P0 Contract and plan: done (2026-10-09)

- [x] `docs/CONTRACT.md`: four rules, sources, states, commands.
- [x] ADR-0065, this file, `CLAUDE.md` records section.

### P1 Visible resolution (rule 2): next

- [ ] `install` prints, per tool, the binary it documents and where its version came from.
- [ ] When the invoking shell's `$PATH` reaches a different copy, `install` says so (`...; this shell runs .venv/bin/ruff`).
- [ ] `--here` documents the copy the invoking shell runs.
  The manifest entry records that binary's path, and `list`/`update` check that copy rather than re-resolving from `$HOME`.
- [ ] A named system binary is refused; the message shows the page `man -w` already finds for it.
- [ ] Integration test through the real CLI, inside an activated venv: the notice, then `--here`.

### P2 `why <tool>` (rule 3)

- [ ] `$PATH`: the login `$PATH` used, the entries the invoking shell has that it left out, and why.
- [ ] Binary: the hit, a shim's target, the installer and its version, or "no installer".
- [ ] Page: what `man -w` shows now, whether maniac owns it, its state and the evidence behind it.
- [ ] Sources: `shipped` candidates, the `upstream` repository and tag result, and what `generated` would use (help commands found, docs found), each with why it was used or passed over.
- [ ] `source crawl` and `source docs` are removed; `why --help-text` / `why --docs` print their raw material.

### P3 `list` is maniac's pages; `update` (rules 1 and 4)

- [ ] `list` rows come from the manifest, one per primary page (the basis is branch `hold/update-path-adr-0065`'s fix).
  Columns: tool, version documented, version installed, state, source.
- [ ] A page with no recorded version reads `unknown`; link drift is shown on its row.
- [ ] `update` reinstalls each `outdated` page maniac installed, reports each, and resumes by being run again.
- [ ] `--managed` goes; `list <tool>` with a name maniac does not manage says so and points to `why`.

### P4 `scan` (rule 3)

- [ ] Discovery across the login `$PATH` moves from unnamed `list` to `scan`, for tools maniac does not manage.
- [ ] States `ok`, `outdated`, `unknown`, `available` and `missing`, with filters as `list` has today.
- [ ] Every skip is counted by reason at the end, so nothing disappears silently: system tools, unclaimed binaries, shims that run nothing, unreadable installer metadata.

### P5 Surface and words

- [ ] `uninstall` becomes `remove`, taking many tools.
- [ ] `--no-synthesize` becomes `--no-generate`; sources read `shipped` / `upstream` / `generated`; `unverified` becomes `unknown`.
- [ ] Help text without internal terms (tiers, providers, "positively proven"), and without the stale `$(maniac status)`.
- [ ] `maniac dev` (hidden) holds `eval` and the benchmark.
- [ ] README rewritten around the contract.

## Decisions taken (2026-10-09)

- Discovery stays, as `scan`.
- A project-only tool is documented only with `--here`, not `--force` (one flag, one meaning); a named system binary is refused with its existing page shown.
- `docs/CONTRACT.md` is the single source of user-facing rules; ADRs only when it changes.
- `source` folds into `why`; `eval` and the benchmark move under `maniac dev`.

## Open

- `--here` and `update`: when the recorded copy is gone (the project was deleted), the page reads `unknown`, and `why` says why; settle in P1 by rule 4.

## Log

- 2026-10-09: P0 landed.
