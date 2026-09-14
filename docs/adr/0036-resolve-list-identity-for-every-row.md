# ADR-0036: Resolve list upstream identity for every row

Status: Accepted
Date: 2026-09-14
Supersedes in part: [ADR-0025](0025-pipeline-local-list-facts-before-upstream.md)

## Context

ADR-0025 deferred two different things behind local evidence and justified both with one cost argument.

The first is repository *identity*: which upstream project a row's tool comes from, rendered in the Upstream column.
The second is the version-matched remote *page probe*: fetching and comparing an actual manpage.
ADR-0025 reversed ADR-0018 on the first and recorded the consequence deliberately, as "an intentional cost reduction and a narrower meaning for the column".

That decision was never consistently in force.
Commit `0228cc7`, "feat: enrich list source provenance", populated Upstream for vendor rows regardless, and `docs/STATE.md` recorded the resulting behavior as intended.
The 2026-09-14 listing refactor implemented ADR-0025 as written, which blanked those cells again and made the contradiction visible.

The cost premise was then measured rather than argued.
On the development machine, `compute_rows` over 68 rows, same commit, identity deferral on versus off: cold 15.924s against 16.209s, and warm runs of 1.161/1.148/1.144 against 1.161/1.147/1.175.
The warm spread within each arm is 1.5 to 2.4 percent, and the difference between the arms sits inside it.
Both arms return the same 68 rows.

The reason is that identity resolution is not the expensive operation ADR-0025 took it for.
Mise's `resolve_source` reads `.mise.backend.toml` or an npm layout from disk, and only falls through to the registry, which `_MiseRegistryLoader` holds process-locally behind a single-flight lock and caches on disk for an hour.
Per row, that is a few file reads and a dictionary lookup.
The registry load it appears to trigger is paid once per process, and is paid anyway by any `missing` row on the same run.

## Decision

Upstream identity is resolved for every row, and the Upstream column is populated wherever a provider can name a repository.
This restores ADR-0018's behavior on this point and supersedes ADR-0025's reversal of it.

The version-matched remote page probe remains deferred exactly as ADR-0025 specified.
That work is genuinely per-row and genuinely network-bound, and no measurement disturbs its rationale.
A locally satisfied row must still not probe.

Everything else in ADR-0025 stands: provider routing by install layout, the immutable manifest snapshot, bounded pools, per-root caches, single-flight registry loading, negative-cache expiry, and the streaming render policy.

## Consequences

The Upstream column means again what ADR-0018 intended: where this tool comes from, for every row that has an answer.
It is no longer the narrower "where a missing page could be obtained" that ADR-0025 defined.

An ADR's cost claim is only as good as the measurement behind it, and ADR-0025 carried none for this half of its decision.
The general lesson is recorded here rather than left implicit: a deferral justified by expense should name the measurement that showed the expense, because the code underneath it can make the claim false without anyone noticing.

Two deferrals sharing one rationale is what let this go unexamined for two days.
They are now separated, and only the probe carries the deferral.
