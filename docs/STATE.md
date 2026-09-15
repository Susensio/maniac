# Implementation State

## Persistent list cache

### Goal

Make warm `maniac list` fast by reusing local facts whose explicit inputs still validate.
Keep its current answer semantics: first-PATH-entry shadowing, manifest ownership, local-manpage reachability, and version-pinned upstream evidence must remain correct.

### Evidence and completed phase

Phase one is complete: `maniac --verbose list` logs manifest, inventory, local-classification, upstream-probe, and total wall-clock durations.
`--verbose` deliberately disables the live table so Structlog keeps its own formatted diagnostic stream; the final static table prints after that stream.
A live 68-row run measured 11.853s total, 0.001s manifest load, 2.898s PATH inventory, 8.948s local classification, and 8.724s upstream probing.
Local and upstream work overlap, so their durations must not be added to estimate total time.
The run encountered transient DNS failures for remote release metadata, which were not cached as absence.

### Implementation order

1. Complete: expose phase timings and pin their debug-log contract in `tests/test_inventory.py`.
2. Build `maniac.cache`, a single typed-fact facade backed by DiskCache (ADR-0041).
   It owns storage, expiry, and concurrent access; fact owners supply stable keys and validate their recorded evidence.
3. Add persistent, validated inventory and provider-detection records.
   Their inputs must include PATH-directory ordering and executable identity, plus provider-specific metadata that establishes package, version, install root, and source.
4. Add persistent, validated local-manpage reachability records.
   Their inputs must include the manifest, manpath configuration, resolver result, and page or man-database fingerprints; a cache miss must retain `man -w` as authority.
5. Move the existing version-pinned upstream cache behind the same facade and recompose rows from validated facts.
6. Benchmark cold, warm, metadata-change, PATH-shadowing, manifest-change, corrupt-cache, and concurrent-process cases before expanding the cache.

### Constraints and deferred choices

Do not skip system PATH directories: it breaks first-entry shadowing and previously had no measurable safe benefit.
Cache external facts, not pure derivations such as `ToolRow`; recomposition is cheap and keeps one answer model.
DiskCache is the selected private storage engine (ADR-0041), while MANIAC validates the source evidence for every reusable fact.
`--no-cache` remains deferred in `docs/BACKLOG.md` until the persistent-cache contract is settled.

## Architecture review follow-up

Waves A, B and C of the 2026-09-14 architecture review are landed and verified.

### Wave A -- landed

Verified as one tree, not one at a time: 584 tests, `just check` exit 0, no import-time
class mutation.

- `d40db9b` split `sources/docs.py` (1272 lines) into a `sources/docs/` package. ADR-0033.
- `ceca643` made manifest loading pure deserialization and moved reconciliation into
  `maniac/lifecycle.py`. ADR-0034. This closed a real defect: `maniac list` could rewrite
  the user's manpath as a side effect of deserializing JSON.
- `dae0011` replaced provider `getattr` probing with typed Protocols and removed Mise's
  import-time class-global resolver callback. ADR-0035.
- `cbdebee` pinned `lifecycle.discard_durable_target`, which an audit found was executed
  by tests but never asserted.

### Wave B -- landed

Verified together at 589 tests, `just check` exit 0.

- `135959b` threaded one `ResolvedTool` through every install tier. `run_pipeline` is
  deleted rather than wrapped; `synthesize` takes 9 arguments where it took 12, and its 58
  statements are split across six helpers. `resolve_tool` is the only constructor of a
  `ResolvedTool`, so tier 3 cannot silently re-resolve what it was handed. Proven by a test
  asserting `find_installation` and `resolve_source` each run once, not twice, with
  `discover_repo` patched to raise.
- `e9f8873` split list inventory and classification into a non-CLI `maniac/listing/`
  package; `cli/listing.py` fell from 1269 to 567 lines and is now a Rich/Typer adapter.
  `compute_rows` takes 3 arguments where it took 11. The nine `on_*` callbacks became one
  `InventoryObserver` receiving immutable ordered snapshots, so an observer cannot steer
  what later rows are classified as.
- `c411dd8` corrected that package's claim to import no Rich. It does, transitively, via
  `..logging` to structlog. The seam is clean; the guarantee as written was not.
- `abe41e7` restored the Upstream column for every row. See below.

### Upstream identity is resolved for every row again

ADR-0025 deferred both repository identity and the version-matched remote page probe behind
local evidence, justifying both with one cost argument. The identity half was never
consistently in force: `0228cc7` populated vendor rows anyway. The refactor implemented
ADR-0025 as written, blanking them, which made the contradiction visible.

The cost was then measured rather than argued. `compute_rows` over 68 rows, same commit,
identity deferral on versus off: cold 15.924s against 16.209s; warm 1.161/1.148/1.144
against 1.161/1.147/1.175. The within-arm spread is 1.5 to 2.4 percent and the difference
sits inside it. Both arms return 68 rows.

Identity is cheap because `resolve_source` reads `.mise.backend.toml` or an npm layout from
disk and only falls through to the registry, which is held process-locally behind a
single-flight lock and cached on disk for an hour -- a few file reads and a dict lookup per
row, and a registry load any `missing` row pays anyway.

ADR-0036 therefore restores identity for every row and keeps ADR-0025's deferral of the
remote page probe, which is genuinely per-row network work. `needs_upstream_identity` and
`_unresolved_locally` were deleted rather than left as pass-throughs.

### Sibling binaries group only in the final table

`maniac list` unfiltered on a TTY streamed every binary separately while
`maniac list --available` collapsed siblings, so the same tools rendered in two shapes
depending on the flags. Pre-existing, not a Wave B regression: the control flow is
identical at `e9f8873^`.

The cause is not an oversight. `_grouped_for_display` keys on
`(provider, package, state, source, upstream)`, and state, source and upstream do not exist
when ADR-0024's streaming skeleton is built -- the skeleton carries every binary up front
precisely so rows stay stable while results land. Collapsing during streaming would make
rows rearrange as they arrive.

The decision was to stream ungrouped and collapse once at completion, in the final render
that already exists separately from the live one. `ae6750d` implements it: the normal-screen
Live is marked transient so the grouped table replaces it rather than following an ungrouped
copy, and the alternate-screen path already discarded its own frames, so one branch covers
both. The live table is unchanged and never regroups mid-run.

On the live inventory this collapses 68 rows to 53, with every binary accounted for, and
`list --available` now renders the same shape for the same tools. No group member disagreed
with its representative on state, source or upstream.

Grouping the default view made a known label defect the common case rather than an edge one
(`python (7 binaries) | missing` above `python (2 binaries) | unverified`), and exposed that
a collapsed row's Source link points only at the representative's page. Both are in
`docs/BACKLOG.md` under "Next round".

### Wave C -- landed

`25fa718` replaced `RepoSource`'s correlated strings with validated local and remote
variants. ADR-0039.
The legacy constructor remains as a validated compatibility boundary, but production
callers consume canonical identity, local path and clone data from the variants.
Remote construction derives the only permitted clone URL from the identity, preserving the
`aqua:` exception while refusing to guess for other backend identifiers.
Verified at 601 tests, `just check` exit 0, plus an independent impact review and a focused
architecture re-audit of direct construction.

`9368b2a` centralized verified source evidence in `sources.candidates`. ADR-0040.
Install-root and repository candidates carry explicit primary pages, per-page provenance,
version evidence and validated target ownership without centralizing consumer policy.
Install retains tier order, listing retains reachable-page classification and remote-probe
coordination, and lifecycle retains its conservative historical migration guards.
Verified at 604 tests, `just check` exit 0, plus an independent impact review and an
architecture audit across all three consumers.

No entries remain under "Architecture review follow-up".
The docs-facade cleanup in "Next round" is now unblocked, its callers no longer being
edited.

The listing inventory seam and resolved-tool context are recorded in ADR-0037 and ADR-0038.

### Housekeeping

`backup-pre-rewrite` (`916213a`) holds the pre-collapse history and can be deleted once the
collapsed history is trusted. The Wave A and B worktree branches are merged and can go. One
worktree admin directory under `.claude/worktrees/` resisted removal and needs
`git worktree prune` from a clean state.

Restructuring leaves observable behavior unchanged and verified, while shape is free
(`CLAUDE.md`). ADR-encoded invariants 0016, 0019, 0023, 0024, 0027, 0028 and 0029-0035 are
preserved rather than re-decided; ADR-0025 is superseded in part by ADR-0036.

ADR-0029 now follows a Mise `latest` vendor manpage only when that alias and the executable both resolve under the exact inspected install root.
Every verified install-root vendor page otherwise links directly to its concrete provider page rather than copying it into MANIAC storage.
Coordinated global Mise upgrades remain `ok`, while a missing, divergent, or unusable alias is `outdated` even when `man` falls through to a system page.
Uninstall removes the MANIAC link but never a provider-owned target, including when its configured output directory encloses that target.

A live disposable Mise probe confirmed that activation is cwd-sensitive: its project selected `bat@0.25.0` and exported a project marker, while `$HOME` selected the global `bat@0.26.1` and cleared that marker.
MANIAC's login-path child runs from `$HOME`, so its successful normal path selects the global direct-`latest` PATH layout.
The development system has no populated Mise shim directory, leaving shim discovery unverified.
When the login-path child cannot initialize its user environment, it currently falls back to the caller's inherited PATH and can therefore reintroduce project activation; that hardening work is recorded in the backlog.

ADR-0028 changed every new managed manpath entry into a manifest-tracked symbolic link.
Generated and repository pages materialize durably under MANIAC data storage rather than linking into disposable cache paths.
Uninstall preserves replaced, retargeted, and dangling entries, and ADR-0032 migrates eligible unchanged install-root copies to direct provider links on manifest load.
The development manifest has no eligible historical vendor copies because its existing `aichat.1` and `ty.1` pages are synthesized and already MANIAC-owned.

`cli.listing.compute_rows` and GitHub release-manpage discovery are now decomposed at their actual coordination boundaries with their observed rendering, callback, ordering, deduplication, and release-validation behavior intact.
`manifest._migrate_install_root_links`, `cli.listing._classify`, and `cli.listing.compute_rows` are all below the configured cognitive-complexity threshold.
Rich listing tests now declare simulated console size and color capabilities explicitly, so Rich version and output-stream detection cannot change the terminal behavior under test.
The 2026-09-14 architecture review found remaining manifest/installer and discovery/resolution soft cycles, plus duplicated source-selection policy, and records their remediation in the backlog.

The default unfiltered TTY view now completes provider enumeration, builds one alphabetically stable per-binary table with every Tool cell populated, then fills State, Source, and Upstream progressively without changing the table shape.
When that table is taller than the terminal, asynchronous rendering is cropped to an alternate-screen viewport and the complete final table is printed once after returning to the normal screen.
Remote-dependent states remain `checking` until their entire deduplicated probe group is finalized atomically.
Filtered, `--names`, and non-terminal output remain final-only so selection and pipelines stay correct.
The State width is derived from every state label plus `checking…`; Tool and Upstream are capped at 24 columns with ellipsis, and neither final nor streaming tables expand Upstream to fill the terminal.

Cargo metadata parsing and pipx home discovery are cached for the life of the process rather than repeated for every executable candidate.
Candidate paths are routed only to providers whose install layout can claim them, with the ordered full registry retained for ambiguous paths.
Local manpage checks run in a bounded pool from one manifest snapshot, and eligible upstream probes begin as individual missing rows become ready.
Install-root inventories are cached per root, cold Mise registry loading is single-flight, and definitive versioned upstream misses use the same five-minute negative-cache policy as missing tags.

A live profile before upstream-result caching measured 35.185 seconds for 77 rows and 35 probes; the final probes each took 2.2–8.6 seconds and caused the visible 97–99% stall.
After `1ceab84`, an empty-cache real `maniac list` completes in 22.40 seconds and a warm run in 5.60 seconds on the development system.
Before the later pipeline work, two warm TTY profiles took 21.00 and 24.47 seconds after imports; Rich publication accumulated 15.17 and 16.50 seconds because every result rebuilt the table, even when no refresh was due.
After routing, overlap, render debouncing and definitive-miss caching, a non-terminal run that populated negative entries took 18.17 seconds and its immediate warm repeat took 9.28 seconds.
On the fully integrated implementation, two warm 120-by-16 TTY runs took 4.76 and 6.04 seconds, and a warm non-terminal run took 6.09 seconds.
After the provenance and compact-column work, an isolated warm 100-by-24 TTY run took 5.08 seconds and entered/exited the alternate screen exactly once.
The remaining profile is dominated by overlapping local/provider work rather than Rich publication: Mise install-root scans, UV editable-source resolution, and npm package metadata resolution.

Skipping `/bin`, `/sbin`, `/usr/bin`, and `/usr/sbin` entirely measured the unsafe upper bound at 5.12 to 3.87 seconds for warm row computation, about 1.25 seconds or 24%, while returning the same 71 rows on this machine.
That shortcut was not retained because it loses first-PATH-entry shadowing and misses system-directory symlinks into managed installs.
A safe experiment kept the names and symlinks but skipped provider routing for 2,089 ordinary system files; isolated enumeration measured 0.551 seconds against 0.547 seconds for the old path, so the complexity produced no measurable gain and was removed.

A live 71-row inventory contains no system-package-owned installation rows, because MANIAC has no apt, pacman, or RPM provider.
It still records names from system PATH directories to preserve first-PATH-entry shadowing, but provider routing does not read those binaries or query their packages.
Package provenance runs only when a provider-managed row resolves an external page.
Debian ownership and versions are cached per process; a proven match reads `ok`, a proven mismatch reads `outdated`, and unsupported or ambiguous evidence reads `unverified`.
The live Mise tealdeer 1.9.0 binary now reads `outdated` against Debian's tealdeer 1.6.1 page, while the unresolved Python and yadm cases read `unverified`.

The legacy Mise npm layout for `bash-language-server` now resolves its explicit `package.json` repository to `bash-lsp/bash-language-server` even without `.mise.backend.toml`.

The live eza probe reports `available` / `upstream` and independently caches `eza.1`, `eza_colors.5`, and `eza_colors-explanation.5` without reading Mise `extra_assets`.

The development system still has no installed `fzf.1` in either MANIAC's data directory or mise's install root. `maniac list fzf` correctly reports `available` / `upstream` from version-matched cached Git objects without a repository worktree.

Mise correctly retains `tmux/tmux-builds` as the binary-distribution provenance for tmux.
Every documentation consumer maps that exact repository to `tmux/tmux` through packaged `defaults.toml`; no repository-name heuristic or Mise `extra_assets` field is consulted.
The mapping is used before list/install manpage probes, `source docs`, and tier-3 documentation extraction.
Help crawling now keeps successful stdout, falls back to stderr, and accepts a failed `--help` only when the combined output contains a real `usage:` line; empty or option-error-only output is a crawl failure.
Tier-3 synthesis may proceed from root help alone or repository documentation alone, warns when only root help is available, and refuses only when neither source produced usable material.
Before the LLM call it reports command and subcommand counts, the resolved repository, documentation-file count, and whether those files matched the installed version.

`list` now keeps content provenance separate from MANIAC ownership: install-root, repository, and synthesis manifest entries render `vendor`, `upstream`, and `maniac` respectively, while `--managed` selects all three.
Each Source keyword links to the exact reachable or shipped local manpage; for GitHub sources, `upstream` links to the version-pinned repository file or release asset rather than MANIAC's cache. Tier-2 installs persist that remote URI in the manifest. The Upstream column continues to link to the repository; unsupported Git hosts keep plain Source text rather than receiving a guessed URL.
Older repository-tier manifest entries recover their missing URI through the same version-pinned, cache-first probe while retaining their installed state.
Repository identity now resolves independently of local page provenance, so vendor rows such as zoxide can still show their upstream project while only missing rows pay for a remote manpage probe.
UV tools now reuse their installed distribution metadata for published packages as well as `direct_url.json` for editable installs; Serena's `Project-URL: Homepage, https://github.com/oraios/serena` therefore resolves its Upstream cell.
Process-local caching reduces repeated UV metadata scans for sibling binaries sharing one tool root from five scans to two on the development system; the isolated editable-source pass measured 0.413 seconds before and 0.132 seconds after.
Full warm TTY runs remained noisy at 6.58-7.75 seconds because transient upstream retries dominated this sandboxed measurement, so no larger end-to-end speedup is claimed.
