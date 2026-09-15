# Implementation State

## Persistent list cache -- abandoned

Measured slower than no cache and dropped; ADR-0045 records the evidence, the negative
findings, and the two conditions that were never measured.
The implementation is archived unmerged at `feature/list-fact-cache` (`604581d`, with the
measurement record at `328c93c`) and was never merged into `master`.

Nothing here is live work.
Read ADR-0045 before proposing a cache for `list` again: three of its findings are reasons a
whole class of fact cannot be cached at all, not incidental details of this attempt.

## Upstream request budget and login-shell environment -- landed

Three changes, all surfaced while benchmarking the abandoned fact cache rather than planned.

`XDG_CONFIG_HOME` is no longer scrubbed from the login-shell child environment (`5af2a97`).
It was never an activation marker, and the isolation it bought belonged to the tests, which now arrange it themselves.
ADR-0044 records the decision, the independent review that argued the other way on ADR-0020 grounds, and the injected-redirect case that is the reason to reopen it.
Activation-marker scrubbing is unchanged.

GitHub API requests reuse the `gh` credential (`b60459d`).
`resolve_github_token()` tries `GH_TOKEN`, then `GITHUB_TOKEN`, then `gh auth token --hostname github.com`, memoized once per process; every failure path degrades to unauthenticated rather than failing the run.
`Authorization` attaches only when the parsed host is exactly `api.github.com`, and is stripped across a redirect to another host.
Verified live outside the sandbox on 2026-09-16: the token resolved, the allowlist admitted `api.github.com` and refused `github.com`, a lookalike host and a subdomain, and a real request through `_download_result` reported a 5000 ceiling rather than 60.
The suite passes identically inside the sandbox and outside it with a real credential present, so no test reads the ambient environment.

One conflated cache TTL became two (`8586e5d`).
`_DEFINITIVE_ABSENCE_TTL` is an hour across three call sites; `_RELEASE_METADATA_REVALIDATION_TTL` is twenty-four hours at the single positive site.
A five-minute window over roughly 26 calls a run demanded about 312 requests an hour against an unauthenticated ceiling of 60.
ADR-0025 carries a dated correction for the two sentences this made false.

### Left unfinished

Cross-host redirect stripping is covered by unit tests only and cannot be exercised end to end.
Release assets are fetched from `browser_download_url`, a `github.com` URL, so they never carry the header to strip.
It is defence in depth against a future caller that authenticates against a redirecting endpoint, not a path production reaches today.

The token change never received the independent review the `coding` skill asks of a finished behaviour-changing feature.

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
Install-root inventories are cached per root, cold Mise registry loading is single-flight, and definitive versioned upstream misses use the same one-hour definitive-absence policy as missing tags.

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

## Parallel backlog wave -- 2026-09-15

Four changes landed on master while the persistent list cache ran on its own worktree, chosen to be disjoint from `maniac/listing/`, `maniac/cli/listing.py`, `maniac/sources/docs/` and cache-related config.
Verified as one tree at 632 tests, `just check` exit 0.

`87a5cbe` resolves cargo upstreams from the installed crate's own registry source.
`CargoProvider.resolve_source` previously returned `None` on the reasoning that `.crates2.json` records no repository and crates.io would have to be queried.
True of `.crates2.json`, false of the disk: `cargo install` leaves the crate source under `$CARGO_HOME/registry/src/<index>/<crate>-<version>/Cargo.toml`, whose `[package] repository` is explicit and offline.
The index directory is a per-machine hash and is globbed, never hardcoded.
Live: `hexyl 0.17.0` declares `https://github.com/sharkdp/hexyl`, and `maniac list hexyl` now renders `sharkdp/hexyl`.

`b27e494` stops `login_path()` handing back the caller's activation-polluted `$PATH`.
`_login_shell_env` always scrubbed the child's environment, but all six fallbacks -- `$SHELL` unset, spawn `OSError`, timeout, non-zero exit, empty output, degenerate probe-only -- returned `os.environ["PATH"]` raw, reintroducing exactly the caller-dependence ADR-0020 excludes.
They now return a sanitized `LoginPath(path, degraded)`, so a caller can tell a degraded answer from a good one; `login_path_dirs()` was deleted rather than left dropping the verdict.
Only `VIRTUAL_ENV` and `CONDA_PREFIX` drive entry removal, because only they name a root an entry can be tested against; an entry with no marker behind it stays.
A live `mise activate bash` on this machine exports `MISE_SHELL`, `__MISE_EXE`, `__MISE_DIFF` and `__MISE_ORIG_PATH`, so `MISE_` and `__MISE_` joined the scrubbed prefixes.
Mise-activated entries still survive sanitization -- no Mise variable names a per-tool root -- and that remainder is in the backlog.

`289ae08` makes uninstall report the durable copy its own migration deletes.
`reconcile()` runs ADR-0032's migration first, which relinks the entry and unlinks the superseded MANIAC copy, so `discard_durable_target` found `provider_target` already set and returned `None`.
`reconcile` now takes an optional `removed` sink that uninstall passes and read paths omit, so a listing command performs the same migration silently.

`db6a4c0` makes a multi-page upstream release one uninstallable installation (ADR-0042).
`Entry.group` carries the primary's manifest key on every member; uninstalling any member takes the unit, checksum-protecting and restoring each displaced vendor page.
`UninstallResult.modified_kept` became a list because protection is decided per member.
`source_uri` was rejected as the grouping key: it records which upstream file, not that pages arrived together, and cannot name a primary.

`96eb8ec` deleted `maniac/sources/__init__.py`'s re-exports, confirmed unreferenced -- the docs-facade cleanup ADR-0033 left blocked on its callers still being edited. No entries remain from that wave.

### The manifest decision

ADR-0043 settles what the manifest is, after SQLite and a filesystem-implicit form were both weighed and declined.
It stays an explicit human-readable JSON record and the source of truth, protected and checkpointed rather than made cheap to lose, with filesystem reconstruction demoted to a best-effort last resort.

The findings that forced the question are recorded in `docs/BACKLOG.md` and none are fixed yet.
`save()` never `fsync`s before its atomic rename.
There is no retained previous generation.
`load()` collapses absent, unparseable and unrecognized-version into `{}`.
`lifecycle._seed_from_headers` is gated on the manifest *not existing*, so a corrupt manifest skips recovery entirely and is then overwritten by the next `record()` with a one-entry file -- the rebuild code is unreachable in the case that needs it.
`_seed_from_headers`' own docstring is stale: it predates ADR-0028, and the symlink target is now stronger evidence than the provenance header it reads.

`SCHEMA_VERSION` is a tripwire with no handler.
An equality check that degrades to `{}` cannot be incremented without making every existing manifest read as empty, which is why ADR-0042 shipped `group` additively rather than bumping it.
Whether it becomes a floor, gains real migrations, or is removed is open.

Whether uninstalling a companion should remove its whole group is also open, with ADR-0042's symmetric removal as the shipped default.

## Manifest machinery rework -- 2026-09-15

Landed on `feature/manifest-rework`, branched from master so master stays a stable rebase target for the list fact cache.
Verified at 668 tests, `just check` exit 0, pytest 24.05s -- faster than the 649-test baseline, so the new per-transaction scan costs nothing measurable.

ADR-0043 settled what the manifest is; ADR-0046 settled how it is written.
A full map of the machinery found considerably more than the four findings ADR-0043 was written against, which is why this was a rework rather than a set of patches.

### What was wrong

`record` and `forget` were each a full load-modify-save, one per page, unserialized: two concurrent installs silently dropped one another's entries while both symlinks stayed on disk.
The manifest was written last, so every crash window left the filesystem ahead of it -- a crash between linking and recording made MANIAC's own page read as foreign, and the next install would back it up as the user's.
`load` collapsed absent, unparseable and unknown-version into `{}`, and silently skipped malformed rows, so one bad row evaporated a tool's ownership and the next save made the loss permanent.
Recovery could not fire in the case that needed it: `_seed_from_headers` was gated on the manifest *not existing*, so a corrupt one skipped it entirely.
Three destructive decisions were independently wrong: `--force` could not uninstall a `target=None` entry, `materialize_target` clobbered another entry's durable target with no collision check, and `discard_durable_target` did not reference-count while its sibling migration did.

### What landed

`manifest.read()` returns `Read(entries, health, reason, lost)` over `Health.ABSENT/INTACT/DAMAGED`; salvaged rows come back whatever the health, and one lost key forces DAMAGED.
`SCHEMA_VERSION` is a floor rather than an equality check, so it can finally be raised -- ADR-0018 and ADR-0042 both had to ship additively because raising it would have emptied every existing manifest.
`save` and checkpoint promotion both `fsync` the temp file and the containing directory around the rename.
`installed.json.good` is promoted on load before mutation, only when INTACT, so a damaged read leaves the last good copy untouched.

`manifest.transaction()` replaces `record`/`forget` under `fcntl.flock` plus a process-local lock, following `sources/docs/cache.py`'s pattern with no new dependency.
Two writes per operation replace N+1, a multi-page release lands in one write or none, and the lock spans the commit phase only so generation stays parallel-safe across processes.

Recovery is layered: salvage the damaged file's parsed rows, then the checkpoint for tools it lost, then link-target reconstruction, with every adopted candidate validated against the disk.
That validation resolves the one real ambiguity -- a checkpoint entry whose symlink is gone is a legitimate uninstall, not a rotted row, and must not be resurrected.
`_seed_from_headers`, `list_installed_manpages` and `read_provenance_header` are deleted; ADR-0028 made the link target stronger evidence than the provenance header.

`--force` now uninstalls a pre-ADR-0028 entry, and `legacy_kept` says "cannot be verified" rather than the false "its bytes have changed".
`materialize_target` refuses a collision naming the owning tool; a rename was rejected because it would need a second identity scheme that four other call sites would have to honour.
`_target_users` is now one implementation shared by both call sites.

### Coverage

The map found no crash window pinned by any test and nothing anywhere running two writers.
Both gaps are closed: `test_two_concurrent_writers_neither_loses_the_other_entry`, plus five install/uninstall interruption tests, several verified to fail with their mechanism disabled.

A Codex review of the recovery work returned five findings; four were confirmed defects and fixed -- unverified provider-root adoption, a lost backup on reconstruction, a `man1`-only scan, and `partition(".")` mis-keying dotted names.

### Known gaps

Two of the four first-cut gaps are closed.
The scan now runs on every `read`, not only on transaction open (`477117b`), so drift is computed on the read path; `Read.links` carries it, and rendering it in the `list` table is what remains.
Reconstructed entries recover their real tier from the provenance header (`aa5b65a`) instead of claiming `Tier.SYNTHESIS` — against real data, all four stamped pages under `~/.local/share/maniac/manpages` recover their correct model, and the live 2-entry manifest scans in 0.157 ms against an 11.8 s `list`.
Still open: tier-1 direct provider links are not reconstructible, and an ADR-0032 migration interrupted mid-relink stays stuck (`BUG:` at `lifecycle.py:276`).
Both are in `docs/BACKLOG.md`, and the second may be moot -- see below.

### The migrations may have nothing to migrate

ADR-0028's `_migrate_links` fires only on entries with `target is None`; ADR-0032's `_migrate_install_root_links` fires only on `tier=INSTALL_ROOT` entries.
The live manifest holds two entries, `aichat` and `ty`, both `tier=synthesis` with targets set.
Neither migration has ever had anything to do on the only machine that exists, and both ran on every install and uninstall this session.
This is pre-1.0 (`CLAUDE.md`: the repository is the whole world, no shims to spare a caller), and the ADR-0032 path carries a permanent-corruption bug.
Deleting both migrations rather than fixing that bug is on the table; it turns on whether any manifest exists on another machine or in a restorable backup.

### Migrations deleted -- 2026-09-15

Both historical migrations are gone (`07b7f56`), with `lifecycle.reconcile` itself: its entire body was the three migration calls.
Net -617 lines across 9 files; `lifecycle.py` fell from 327 to 120 and lost the module's only file-deleting code paths.
`select_historical_install_root` went with them, and the ADR-0032 `BUG:` marker disappeared with the code that carried it.

The premise was verified before deleting, twice.
ADR-0028's `_migrate_links` fires only on `target is None`; ADR-0032's `_migrate_install_root_links` only on `tier=INSTALL_ROOT`; `_migrate_backups` only on a stray `*.maniac_bak` in `man_dir`.
The live manifest holds `aichat` and `ty`, both `tier=synthesis` with targets set, and no stray backup exists.
Neither guard could ever have fired, and this is the only MANIAC installation there is -- solo tool, solo developer, pre-1.0, `CLAUDE.md`'s "the repository is the whole world".

`reconcile`'s `removed` sink went too, undoing `5edb89d`. That commit fixed a real defect -- uninstall deleting a migrated file without reporting it -- but with no migration there is no such file.
`UninstallResult.removed` stays: live removals (backups, orphaned roff, purge artifacts) still populate it.

Verified at 663 tests, `just check` exit 0, and against real data: `maniac list` works on the live manifest, and an install/uninstall round trip on a throwaway config records and removes correctly with `reconcile` absent from the path.

Five `tests/test_installer.py` fixtures were not migration tests by name but silently depended on migration converting a plain-file legacy record into a linked entry before uninstall would treat it as removable; they now build entries the way `install_manpage` actually produces them.

That exposed a live consequence worth watching.
A `target=None` entry is now permanently `legacy_kept` -- never auto-removed, never restored from backup -- unless `--force` is given, which `e3287fa` made work.
Nothing MANIAC does can create such an entry any more: `install_manpage` always sets `target`, and recovery reconstructs from link targets.
It is reachable only from a hand-edited or foreign manifest, so the branch is now defensive rather than migratory and was deliberately kept.

### Reconciled with the abandoned cache -- 2026-09-16

The persistent fact cache was abandoned (ADR-0045) while the manifest rework ran on its own branch.
That branch existed to keep master a stable rebase target for the cache work, so its reason is gone; the 17 manifest commits are rebased onto master and the branch is retired.

Three sessions allocated ADR numbers concurrently and none could see the others' trees.
0044 went to the XDG pass-through decision, 0045 to abandoning the cache, and the manifest transaction ADR moved twice before settling at 0046.
Allocating a number before a decision lands is what causes this.

Two of the manifest rework's backlog items were blocked on the cache landing and are now simply open: rendering drift in the `list` table (`Read.links` computes it, nothing shows it), and the `entry.version` -> `documented_version` rename.
The third, `_grouped_for_display`'s dead `pending` parameter, is likewise unblocked.
