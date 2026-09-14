# ADR-0037: List inventory behind an observer seam

Status: Accepted
Date: 2026-09-14

## Context

`cli/listing.py` had reached 1269 lines holding four unrelated jobs: candidate enumeration and local classification, upstream probe scheduling and deduplication, Rich table rendering, and Typer command wiring.
`compute_rows` took eleven arguments, nine of which were `on_*` progress callbacks.

Because the callbacks were positional arguments to the function that also decided classification, a renderer could reach the coordinator's live row list while later rows were still being classified.
Terminal refresh timing and progress reporting were therefore in a position to steer classification facts, and nothing in the design prevented it.

ADR-0024's stable streaming rows and ADR-0025's local-first ordering were both real contracts, but the only way to exercise them was through a terminal, which made them expensive to test and easy to break silently.

## Decision

Inventory becomes a non-CLI package, `maniac/listing/`, holding models, local classification, upstream identity and probes, and the row coordinator.
It does not live under `cli/`, because a service the CLI consumes is not part of the CLI.

`cli/listing.py` is an adapter: Rich tables, the `Live` lifecycle, column geometry, filtering by flag, and the Typer command. It fell to 567 lines and holds no classification logic.
`compute_rows` takes three arguments.

The nine callbacks collapse into one `InventoryObserver` base class whose methods default to no-ops.
The coordinator publishes `tuple(self.rows)` — a genuinely immutable ordered snapshot — so an observer can neither mutate what later rows are classified as nor retain a reference to the list still being built.
A single `_TerminalObserver` in the CLI is the only adapter, and it carries the one piece of real policy (row progress stops once a live table exists) as a field rather than as conditional arguments at four call sites.

Grouping stays on the rendering side and is named for what it is, a display collapse, rather than sitting among the classification facts it visually merges.

`local_facts_ready` is kept although nothing in the CLI consumes it.
It is ADR-0025's local-first ordering expressed at the seam, and it is what the snapshot test asserts against.

## Consequences

ADR-0024's streaming contract and ADR-0025's ordering are now testable without a terminal, and terminal tests cover only rendering.
The listing suite fell from roughly 72 seconds to 20, because classification tests stopped doing real provider work to reach the behavior they were asserting.

An observer cannot influence classification, which was previously prevented only by everyone being careful.

The package documents that no module in it renders or reads a console.
It does not claim to keep Rich out of `sys.modules`: `upstream` imports `..logging`, which imports structlog, which imports Rich itself. The seam is about what this code does, not about what its logging dependency drags in.

`resolution.enumerate_installations` still takes two loose `on_start` and `on_scan` callables rather than the observer. It was out of scope, and threading the observer through it is the natural follow-up.
