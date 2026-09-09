# Implementation State

## ADR-0018: offline core landed, three pieces open

[ADR-0018](adr/0018-list-reports-manpage-reachability.md) is accepted and now partly implemented.
This session landed its offline core; the network, responsiveness and repair pieces remain open and are tracked in `docs/BACKLOG.md`, not here.

### What landed

`status` is now `list` (`maniac/cli/listing.py`, was `maniac/cli/status.py`).
Its State column answers whether `man <tool>` works rather than what MANIAC has done, via the four-rung ladder `ok` / `outdated` / `available` / `missing`.
The single wiring change behind it: `manpages.find_installed_manpage_path` — which already existed for `eval --against-installed` and had never been called from this path — now runs for every row, reversing ADR-0013/ADR-0016's "the manpath is never scanned".

The table carries four columns, Tool / State / Source / Upstream.
Source is a `PageSource` enum rendering `maniac`, `install-root`, `system` or blank; Upstream is the resolved `RepoSource`, blank when none was resolvable.
Filters `--outdated`, `--available`, `--missing` (State) and `--managed` (Source) union within an axis and intersect across, and are applied before the render branch so the table and the piped bare-name output can never disagree.
There is no `--ok`, deliberately.

The manifest gained `version: str | None` on `Entry`, threaded from `install_manpage` through both `manifest.record` call sites.
`SCHEMA_VERSION` was deliberately **not** bumped: `load()` discards the whole manifest on a version mismatch, which would have destroyed the two live entries. An absent `version` reads `None` and can never make a row `outdated`, which is ADR-0018's "positive evidence" rule holding correctly rather than a workaround.

### What did not land, and why

Upstream is resolved **offline only** — from installation-derived metadata via `Provider.resolve_source`, which names the repository without touching the network.
ADR-0018 commits to going further and checking upstream for a page at a matching version (`git ls-remote --tags` plus a shallow clone); that is a separate backlog entry and is not implemented.
So `available` currently means "a page ships in the install root", never "upstream has one".

Mise is the one provider whose `resolve_source` can reach the network, through `_query_mise_registry`'s registry fallback on a cache miss.
It is called with `offline=True` from `list` only, so the local `.mise.backend.toml` scan still runs and the registry fallback is skipped; `install` is unchanged and may still use the network.
A test pins this by making `urlopen` raise and asserting a full pass still resolves.

### Behaviour change worth knowing

A MANIAC-managed page whose file exists but which `man` does not resolve — `man_dir` absent from `MANPATH` — now reads `available` or `missing`, not `managed`.
That is correct under the new definition, since `ok` asserts reachability, but it is a visible reversal for anyone whose `MANPATH` is not set up, and it is pinned by an explicit test.
More generally, two machines with identical binaries and different `MANPATH` settings will legitimately report different states.

### Verified

`just check` passes: 379 tests, ~14s.
Live on the development system, 70 rows: 5 `ok`, 0 `outdated`, 14 `available`, 51 `missing`, with 45 of 70 carrying an Upstream.
A full run makes no network call, confirmed by making `discovery.urlopen` raise and observing the run complete with Upstream still populated.
Wall clock is ~3.5-3.8s for the full CLI invocation (~3.0-3.3s in `compute_rows`), against ~1.4-1.6s for the `$PATH` walk alone.

`outdated` reads 0 and cannot currently be anything else on this machine: both live manifest entries predate the version field, and the tier-3 synthesis path still records no version at all.
Both are backlog entries. The state itself is reachable and tested — it is the data that is missing, not the code.
