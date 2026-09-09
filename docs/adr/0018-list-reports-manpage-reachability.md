# ADR-0018: Rename status to list and report manpage reachability instead of MANIAC's action history

Status: Accepted
Date: 2026-09-09

## Context

`status` reported three states, fixed by ADR-0016: `available` (a page shipped in the install root, not yet installed), `missing` (no page anywhere), `managed` (MANIAC installed it).
All three answered one question — what has MANIAC done for this binary — and none answered the question a user brings to the command, which is whether `man <tool>` works.

A run on the development system made the gap concrete: 51 rows, 37 `missing`, 13 `available`, 1 `managed`.
`bat` reported `missing` while `man bat` resolved a distro page perfectly well.
`missing` meant "MANIAC has taken no action and holds no free copy", and a reader could not tell that from "this tool has no manual", which is what the word says.

The blindness was deliberate.
ADR-0013 and ADR-0016 chose not to scan the manpath, and `maniac/cli/status.py` recorded the choice in its module docstring as "The manpath is never scanned".
The reasoning held that the unit was a binary a provider detected rather than a page found on disk, because the point was capability — what a provider knows that the manpath cannot.
That was the right axis for deciding what MANIAC could act on, and the wrong one for reporting coverage.
`manpages.find_installed_manpage_path` already existed and was already used by `eval --against-installed`; it had simply never been wired into `status`.

Tier 2 was never attempted at status time either, so `missing` further conflated "needs an LLM" with "nobody looked upstream".
Resolving tier 2 required `git ls-remote` against an installation-derived clone URL and, on a tag match, a shallow clone, with results cached under the configuration's cache directory as `<name>@<tag>`.
Binaries whose upstream repository could not be resolved from their installation — which ADR-0008 and ADR-0015 require, guessing from the binary name being forbidden — needed no network call at all.

The name was surveyed against installed CLIs and published convention.
No tool was found that uses `status` for a system-wide survey of unrelated items: `git status`, `systemctl status` and `gh status` each describe one entity, or a workflow the user is party to.
`apt list` was found to span both installed and available packages, which is precedent `pip list` and `mise ls` — inventory-only — do not set.
`check`, `audit` and `scan` were rejected as names: each was strongly claimed elsewhere, by test and lint suites, by security vulnerability reporting, and by vulnerability scanning respectively.
`survey` and `coverage` were weighed and discarded — `survey` because being unclaimed by any tool also made it unguessable, `coverage` because it read poorly in the pipe composition that ADR-0013 established as the bulk-install path.

ADR-0017's manifest recorded a page's path, tier, source, checksum and backup, and no version.
Nothing therefore recorded what version a managed page documented, so no page's staleness was answerable from stored facts, even where the installed binary's version was in hand — as it was for most providers, `local_lib` never being among them.

Two facts about a binary were being conflated under one heading: where the binary came from (its provider) and where its page came from or would come from (a distro, the install root, an upstream repository, MANIAC itself).
Both were cheaply known and only one was shown.

Separately, `_grouped_for_display` labelled a solo group by its tool name and a multi-binary group by its package name, so one package split across two states rendered two rows a reader could not tell apart — observed as `node | available` directly above `node (2 binaries) | missing`.
That defect is recorded in `docs/BACKLOG.md` and is not decided here.

## Decision

`status` is renamed `list`, and its State column answers exactly one question: what, if anything, should be done about this tool.

Four states, defined by reachability rather than by MANIAC's history, and ordered as an action ladder:

1. `ok` — a page resolves through `man` now, from any source whatever, and nothing suggests it is stale;
2. `outdated` — a page resolves, but MANIAC installed it and the version it documents no longer matches the installed binary, so reinstalling replaces it;
3. `available` — nothing resolves, and a page can be had without an LLM, from the install root or from upstream at a matching version;
4. `missing` — nothing resolves and no free page is known, so synthesis is the remaining path.

`outdated` requires positive evidence: a recorded page version that differs from the binary's current version.
Absent that evidence a row reads `ok`, because MANIAC is not entitled to call a page stale it never assessed.
This covers the permanent case as well as the transitional one -- `local_lib` never reports a version, so a page for a raw checkout can never be shown outdated, and reads `ok` rather than acquiring a state of its own.

Upstream is resolved on every run rather than behind a flag.
Binaries with no installation-derived repository make no network call and reach a final state at once.
How resolution is presented while it is still in flight -- whether the table blocks until every row settles, renders immediately with a per-row pending marker, or streams -- is deliberately not settled here.
The interface is fixed first and the responsiveness work second, so the loading behaviour is designed against a column layout that has stopped moving.

The table carries four columns: Tool, State, Source, Upstream.
`Source` names the kind of origin in one column for both meanings it must carry — for an `ok` row it is where the page came from, for an `available` row it is where a page would come from — because State already disambiguates which reading applies, and splitting them produced two columns each blank for most rows.
`Upstream` names the resolved repository; blank means none was resolvable, which is also the explanation for a `missing` row.

Freshness is a State value rather than a column of its own.
A separate column would be populated only for rows that are both reachable and MANIAC-owned — one row in seventy on the development system — and a column blank for every other row is not a column.

The manifest gains a version field recording the tool version a page documented.
Entries predating that field are repaired rather than tolerated: MANIAC re-derives each page it owns and records the version it actually came from, so no entry is left permanently unassessable and the interface needs no transitional value to describe one.
Stamping an old entry with today's binary version was rejected as the cheaper alternative, because it asserts a freshness nobody checked; re-deriving makes the recorded version true.

One table serves both the survey and the inventory, narrowed by a filter per actionable State — `--outdated`, `--available`, `--missing` — plus `--managed`, which filters Source rather than State.
There is no `--ok`: it would select exactly the rows needing no action, which is not a thing anyone asks a tool for, and its absence is what makes the filter set read as a worklist.
Filters on one axis union and filters across axes intersect, so `--available --missing` is every row worth acting on and `--managed --outdated` is MANIAC's own stale pages.
Piped output emits every row the table shows rather than a filtered subset, so what is displayed and what is piped never disagree; narrowing a bulk install is done explicitly with a filter.

Cost is not a column. It is derivable from State and does not earn width.

This reverses ADR-0013 on two points it settled, and does so deliberately.
ADR-0013 replaced `list` and `list-missing` with `status`; the objection recorded there was to a **pair** of near-duplicate commands that had drifted apart in their defaults, `list_missing` alone running to 208 lines. One command narrowed by flags does not recreate that pair, so the objection does not reach the name.
ADR-0013 also rejected a `--missing` filter because "missing" named an adjective with no noun behind it. Under this record `missing` is a named state with a stated definition, so the adjective has its noun and the objection dissolves.
ADR-0013's substance is otherwise untouched: there are still no bulk subcommands, and composition through pipes remains the bulk path.

ADR-0016's tier order is unchanged, and its exclusion of quality judgement is retained in full: an `ok` row asserts that a page resolves, never that it is any good.
ADR-0012 is followed rather than reversed — every state and column here is computed at display time from stored facts, and no verdict is persisted.

## Consequences

The command answers the user's actual question, and the most common wrong answer disappears: a tool with a working distro page stops being reported as `missing`, and the reader stops being invited to pay for an LLM to duplicate a manual they already have.

A command that was a pure filesystem walk becomes network-dependent.
This is the largest cost of the decision: `list` can now be slow to settle, can fail partially, and behaves differently offline — none of which was true of `status`.
Until the deferred responsiveness work lands the simplest implementation blocks, which is the worst version of that cost and is accepted only as a starting point.
The blast radius is bounded by how many binaries resolve an upstream repository at all, which was not measured before this record and should be measured before the concurrency is designed.

The documented bulk-install one-liner grows a filter.
`maniac status | xargs maniac install` fed only tools MANIAC could act on; the filters are what carry that meaning now, so the README's example becomes an explicitly narrowed form.
The unfiltered pipe remains valid and targets every row, which is the point: what the table shows and what the pipe emits never disagree.
Whether the filters combine as a union, or whether a single flag should name the actionable set, is left to implementation.

Repairing existing managed pages is not free.
Both entries on the development system are tier-3 synthesis, so re-deriving them costs an LLM call each; a machine with many managed pages would pay proportionally.
That cost was accepted over carrying an `unknown` value through the interface, on the grounds that a migration gap should not shape a permanent surface.

Staleness stays computable only for pages MANIAC owns.
A distro page can be just as stale and will always read `ok`, because manpages carry no reliable version field and nothing records what a vendor page documents.
Two reachable rows are therefore treated differently by who installed them, which folding freshness into State makes less visible than a separate column would have.

Renaming breaks muscle memory, the README, and shell completions, and it re-decides a name this project already changed once.
The record of that reversal is this ADR; without it, a future reader finding `list` would reasonably conclude ADR-0013 had been ignored.

Reversing "the manpath is never scanned" costs the property that made it attractive: enumeration was previously provider-only and therefore explainable entirely in terms of what providers knew.
Coverage now depends on `man`'s own configuration, so two machines with identical binaries and different `MANPATH` settings will legitimately report different states.
