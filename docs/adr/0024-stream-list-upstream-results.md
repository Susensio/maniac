# ADR-0024: Fill a stable list table while availability loads

Status: Accepted
Date: 2026-09-12

## Context

ADR-0018 left the presentation of in-flight upstream resolution deliberately unsettled.
The first implementation showed one combined progress bar until every row was final, then printed the table.
A live run scanned 2,728 executable names to find roughly 72 provider-owned rows and then waited on remote probes; even after caching reduced a warm run to 5.60 seconds, the screen stayed blank except for progress and appeared stuck at 97–99% while the last rows settled.
Most table facts were already known before those probes finished, so withholding every row made remote tail latency look like total command latency.
An initial implementation appended rows during provider discovery and regrouped them when complete, but changing the table height and order caused visible terminal flicker.

## Decision

The default interactive `list` view will complete provider enumeration, then build one complete, alphabetically ordered per-binary row model before local manpage classification and upstream identity resolution.
If the model fits in the terminal, every Tool cell is rendered in the first frame.
Rows whose local or upstream availability is unresolved will say `checking` rather than temporarily claiming `missing`; only State, Source, and Upstream will change as facts arrive.
Rendering updates will be coalesced at a bounded refresh rate rather than forcing a full terminal repaint for every completed row.
Row count, order, labels, and grouping will remain fixed for the lifetime of the live table.
If the row model exceeds the terminal height, the live phase will render a stable leading slice plus the number of hidden tools in a cropped alternate-screen viewport, so layout and updates are bounded to one screen.
After that viewport closes, the complete final table will be printed once on the normal screen; a short table remains in place directly without a duplicate rendering or final regrouping.

Filtered, `--names`, and non-terminal output will wait for final classification before emitting anything.
Those modes define membership or machine-readable output from final states, so provisional output would be incorrect or would break pipelines.

## Consequences

An interactive user can read the discovered tool names that fit in the viewport while local checks run, then local states and upstream identities while network work continues; the slow row is visible instead of being represented by an unexplained percentage.
The stable table avoids the height, ordering, and grouping changes that made incremental row discovery flicker.
The alternate screen prevents a tall table from repeatedly rewriting scrollback, at the cost of showing only one viewport during loading; the full result remains available after completion.
The first frame still waits for provider enumeration, so repeated Cargo and pipx metadata work is cached for the process to shorten that phase without introducing a stale persistent inventory.
The rendering path needs a live table, immutable row snapshots, stable per-binary identities and refresh coalescing while worker threads update results.

Pipes and filtered interactive calls retain their previous latency before first output.
That delay is accepted because correctness and stable machine-readable output matter more than progressive display in those modes.
