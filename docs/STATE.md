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
It was initially called with `offline=True` from `list`, to hold the offline property this pass was scoped around; that gate was lifted shortly after — see the ADR-0019 section below, which records what replaced it.
The `offline` parameter itself stays on `_resolve_from_mise` and `MiseProvider.resolve_source`, unused by `list` but tested, because ADR-0018's deferred tier-2 work has to decide about it deliberately.

### Behaviour change worth knowing

A MANIAC-managed page whose file exists but which `man` does not resolve — `man_dir` absent from `MANPATH` — now reads `available` or `missing`, not `managed`.
That is correct under the new definition, since `ok` asserts reachability, but it is a visible reversal for anyone whose `MANPATH` is not set up, and it is pinned by an explicit test.
More generally, two machines with identical binaries and different `MANPATH` settings will legitimately report different states.

### Verified

Measured at the point the offline core landed, before the gate was lifted: 70 rows, 5 `ok`, 0 `outdated`, 14 `available`, 51 `missing`, 45 of 70 carrying an Upstream, no network call at all, ~3.5-3.8s for a full CLI invocation (~3.0-3.3s in `compute_rows`) against ~1.4-1.6s for the `$PATH` walk alone.
Those figures were taken through `uv run` inside this repo, where `.venv/bin` shadows some real binaries — `docs/BACKLOG.md` records the caveat and the resolver divergence behind it.

## ADR-0019: implemented, one page left to regenerate

[ADR-0019](adr/0019-earn-synthesized-page-version.md) closed the gap ADR-0018 left: tier-3 synthesis extracted repository documentation from the default branch while claiming to document the installed binary's version.
Tier 3 now resolves and clones the matching tag, records the version when one is found, and records none when it is not.
`fetch_and_extract_docs` returns `(docs, matched)` so the "was it tag-matched" fact has a single source of truth rather than being inferred from `inst.version`.

The mise offline gate is gone. `list` calls `provider.resolve_source(inst)` uniformly, so the registry fallback may reach the network; measured at 45→49 of 70 rows gaining an Upstream, one ~90KB fetch per run, `timeout=10` already configured, and every failure mode degrading to a blank Upstream rather than erroring.
`list` is therefore no longer guaranteed offline, which is what ADR-0018 anticipated and accepted.

Verified end to end on the development system: regenerating `aichat` produced a tag-matched clone at `v0.30.0` and a manifest entry recording `"version": "0.30.0"`, and the page renders correctly through `man`.

**Unfinished:** `ty` was not regenerated. Four attempts returned `litellm.ServiceUnavailableError` — Gemini 503, "experiencing high demand" — so its entry still reads `version: null` and it cannot show `outdated`.
Nothing is broken; the work simply did not complete. Re-run `maniac install --generate ty` when the API recovers.
The pre-regeneration pages and manifest were snapshotted to the session scratchpad, which does not survive indefinitely — `ty`'s page on disk is untouched, so nothing needs restoring.

`just check` passes: 384 tests.
