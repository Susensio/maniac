# ADR-0013: Compose status and generate through pipes instead of bulk subcommands

Status: Accepted
Date: 2026-09-04

## Context

`cli.py` had grown to 834 lines across nine commands, of which `list`/`list-missing` and `generate`/`generate-missing` were two near-duplicate pairs; `list_missing` alone was 208 lines.
The pairs had drifted: `generate` defaulted `install=False` while `generate-missing` defaulted `install=True`, for the same pipeline.
`--install` guarding installation made the flag a precondition for the tool's purpose, since a generated page that is not installed does not answer `man <tool>`.

The bulk commands existed because there was no way to feed a computed list of tools into `generate`.
`--include-candidates` conflated two axes in one flag, widening both the scan scope and the set of statuses shown, which is why `--scope`/`--only` as separate flags were also rejected: the scopes are not independent of the statuses, since scanning executables can only find pages that are absent and scanning the manpath can only find pages that exist.

A fifth verb (`sync`) and a `--missing` filter were both weighed and discarded, the first as an extra concept and the second because "missing" named an adjective with no noun behind it.

## Decision

`status [TOOL...]` replaces `list`, `list-missing` and `--include-candidates`.
With no arguments it reports the tools MANIAC could act on — those with a source resolvable under ADR-0005 and ADR-0008's installation-derived rule, plus those MANIAC already manages.
With arguments it reports exactly those tools and applies no filtering, which is the escape hatch that removes the need for `--bin-dir`, `--system` or `--all`.
The scan performed is implied by the filter requested rather than named separately.

There is no bulk generation command.
`status` writes bare tool names when stdout is not a terminal, so `maniac status --only … | xargs maniac generate` and `maniac generate $(maniac status --only …)` are the bulk path; `generate` with zero tools exits 0 rather than raising a missing-argument error.

Installation becomes the default, with `--no-install` to stop after compilation.
`compare` folds into `eval`; `crawl` and `docs` move under a `source` group.
`cli.py` is split into modules under `maniac/cli/`, with shared options declared once.

## Consequences

Nine commands become four plus a group, and the two pairs can no longer disagree about a default.
Bulk runs lose the per-tool progress display, since the driving loop moves to the shell.
`generate-missing` and `list-missing` disappear as names.

The default scan is broader than the previous `~/.local/bin`, which ADR-0012's cache is what makes tolerable.
The filter vocabulary that `--only` accepts is not settled by this record; ADR-0012 defers it to real output.
