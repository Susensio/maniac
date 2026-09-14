# ADR-0025: Pipeline local list facts before upstream probes

Status: Superseded in part by [ADR-0036](0036-resolve-list-identity-for-every-row.md)
Date: 2026-09-12
Supersedes in part: [ADR-0018](0018-list-reports-manpage-reachability.md)

ADR-0036 restores upstream identity resolution for every row: the cost this ADR
avoided was measured and found to be inside run-to-run noise. The deferral of the
version-matched remote page probe, and every other decision below, still stands.

## Context

ADR-0018 made `list` network-dependent and required repository identity to be resolved on every run, while leaving responsiveness unsettled.
The first implementation formed a serial chain: scan every executable through every provider, classify every local manpage, resolve every repository, then start upstream probes.

A live profile found 2,728 executable candidates but only 72 provider claims.
All claims arrived early, while thousands of impossible provider checks delayed the complete inventory.
Across two warm runs, local `man -w` and install-root work, upstream probes and Rich publication each contributed substantial aggregate time.
Rich rebuilt a tall table for every result even when its refresh throttle suppressed terminal output.
The upstream cache also knew how to read a definitive empty result but never wrote one when a matching tag contained no page.

System paths do not supply a safe shortcut.
MANIAC has no system-package provider, and a path under `/usr/bin` or `/usr/local/bin` neither proves package ownership nor guarantees a manpage across distributions.
The safe fast-fail evidence is whether an installer provider's layout can claim the resolved path.

## Decision

Provider discovery will route a resolved candidate only to providers whose installation layout can plausibly claim it.
Ambiguous paths and extension providers retain the complete registry in declaration order, so routing cannot replace installation-derived evidence or name a source by guesswork.

One `list` run will load one immutable manifest snapshot.
Local manpage classification will use a bounded worker pool while preserving the sorted row model and invoking renderer callbacks only from the coordinator.
Bounded install-root file inventories and provider metadata may be cached for the process when their inputs are explicit; cold Mise registry loading will be single-flight.

Repository identity will be resolved only for a row that has no reachable or install-root manpage.
Upstream availability will then be probed only when that row also has enough versioned installation evidence for tier 2.
This reverses ADR-0018's requirement to resolve repository identity for every row.
An `ok` or install-root `available` row is already final, and spending registry or network work to decorate its Upstream cell does not improve the reachability decision.

An eligible upstream probe will start as soon as its local row is ready rather than waiting for all local classification.
Probe work remains bounded and deduplicated by its versioned source identity; every sibling row receives the completed result atomically.

Positive versioned probe results persist.
Definitive empty results and missing tags expire after five minutes because a tag or release asset may be published after the binary appears.
Mutable GitHub release metadata is revalidated on the same interval, while downloaded immutable asset bodies remain persistent.
Transient Git, DNS, timeout, server and malformed-response failures are never cached as absence.

Live rendering will update its row model for every result but rebuild the Rich renderable only when a refresh is due, on a coordinator idle tick, and once at completion.
Tall live tables render only one stable viewport; the complete table is printed once after completion.

## Consequences

Local work overlaps instead of forming barriers, and network tail latency no longer prevents already-known cells from appearing.
Warm negative results fail fast without turning transient outages into false `missing` states.
Provider routing avoids work on ordinary system candidates without assuming that system packages carry manpages.

Locally satisfied rows now leave Upstream blank even where their provider could resolve a repository.
That is an intentional cost reduction and a narrower meaning for the column: it explains where a missing local page can be obtained, rather than decorating every row.

The complete first table still waits for provider enumeration because a persistent inventory would need reliable invalidation for login `PATH` order, symlink targets, install roots and provider metadata.
A future system-package provider must use native package ownership and actual manpage reachability, not path prefixes or a cross-distribution assumption.
