# Backlog

Open work with no single line to mark.
`rg -n 'BUG:|TODO:'` lists the rest.

## Next round

Yielded by the 2026-09-14 architecture wave (ADR-0033, ADR-0034, ADR-0035).
These are findings the work surfaced and deliberately did not take; they are first, not filed.

### Correctness

- Disambiguate list rows whose grouping renders the same visible label for different binaries or states.
  Preserve bare-name pipeline output; choose either package-plus-binary labels or a width-capped binary list.
  Now the common case rather than an edge one: the default unfiltered table groups since
  2026-09-14, and the live inventory renders `python (7 binaries) | missing` above
  `python (2 binaries) | unverified` -- two rows no label tells apart.
- Key a display group on its members' pages, or stop linking Source on a collapsed row.
  A group's Source hyperlink is the representative's page alone: `page_path` and `page_uri`
  are not in the group key, unlike state, source and upstream. On the live inventory 5 of 68
  binaries link somewhere other than the row now standing for them -- `npm`'s own `npm.1`
  under `node (3 binaries)`, `pandoc-lua.1.gz` under `pandoc (3 binaries)`.
  Pre-existing and previously reachable only through a filtered view; grouping the default
  table made it the common case.
- Decide what `maniac list` should print when stdout is not a terminal.
  It currently renders the Tool column alone, because Rich falls back to 80 columns and the
  other three are dropped; `--names` already exists for the bare-name pipeline case, so this
  looks like a rendering accident rather than the intended pipe output.
  Candidates are a plain-text width, a `--plain` mode, or `--json`.
- Assert `lifecycle.link_manpath_entry`'s atomic replacement, not just its end state.
  Current tests pin that the manpath entry ends as the right symlink; nothing pins that a pre-existing entry is replaced atomically rather than unlinked and recreated.
  Needs an interleaving harness the project does not yet have.

### Structure

- Remove `_grouped_for_display`'s dead `pending` parameter.
  No caller passes it, so the `row.tool in pending` element of the group key is constantly
  `False` and the parameter silently widens the key for nobody.
- Thread probe definitiveness back as a return value instead of `docs.cache`'s module-level `_lookup_state` thread-local.
  ADR-0033's split made that cross-module channel visible without removing it: `cache` writes it and `repository` reads it.
  Removing it touches every probe signature, so it was left out of the split deliberately.
- Replace `installer.install_manpage`'s and `manifest.record`'s eleven-parameter signatures with one entry record.
  Both describe the same installed page and drifted into parallel positional lists; ADR-0034 moved their coordination but not their shape.
  Both still trip the raised `max-args = 10`, which is the point of that threshold: eleven parameters is coordination, seven is a command surface.
- Split `sources/docs/release._fetch_and_materialize_release_asset`'s direct-asset and archive-asset flows.
  They are two flows sharing one function, which is why it still carries seven returns after ADR-0033.

### Coverage

- Give `cli/listing._provider_target_freshness`, `_build_inventory`, `_try_install_root` and `_try_repository` direct unit tests.
  They are reached only through `compute_rows` and `run_install` today, so a change in their own behavior need not fail anything.
  ADR-0035 made the freshness capability explicit, which makes these locally testable for the first time.
- Split `tests/test_docs.py` to mirror the five modules behind the ADR-0033 facade.
  It is 1301 lines against a package whose patch targets are now per-module; the split rehomed the targets but not the file.

## Bugs and correctness

- Distinguish a definitive tier-2 absence from a transient repository probe failure.
  The latter must not silently fall through to synthesis, which can conceal a wrong repository or a network, tag, tree, release, or validation failure.
  Report the consulted repository and tier immediately; then decide between interactive confirmation and uniform refusal with an explicit synthesis override.
  This also decides whether `install --generate` remains necessary: ADR-0016's authoritative-first order makes forcing tier 3 normally worse, but it may be the deliberate replacement escape hatch.
- Distinguish a wrong documentation repository from one that legitimately has no manpage.
  Flag-inventory overlap and whether the repository contains implementation source are possible evidence, but absence is a normal synthesis fallback and must not be treated as proof of misresolution.
- Drop Mise-activated `$PATH` entries on a degraded login-path fallback, as venv and conda entries already are.
  The generic half is done: every `login_path()` fallback now returns a sanitized `LoginPath(path, degraded)` rather than the caller's raw `$PATH`, and `MISE_`/`__MISE_` joined the scrubbed prefixes.
  Removal needs evidence, and only `VIRTUAL_ENV` and `CONDA_PREFIX` name a root a `$PATH` entry can be tested against.
  No Mise variable names a per-tool root; `~/.local/share/mise/installs/...` would have to be reconstructed by convention, which is the guessing this rule exists to forbid.
  `__MISE_ORIG_PATH` records the entire pre-activation `$PATH` and is the plausible evidence source -- a live `mise activate bash` on this machine exports it alongside `MISE_SHELL`, `__MISE_EXE` and `__MISE_DIFF`.
  Using it is Mise-specific, so decide whether the fallback gets per-provider evidence adapters or stays generic.
  Shim resolution remains unexercised: this machine has no populated shim directory, and `mise which -C $HOME` is cwd-sensitive and needs ADR-0029-style root validation.
- Settle the manifest's durability, recovery and concurrency contract as one decision.
  Supersedes "serialize manifest load-modify-save with a sidecar lock": locking is one of four findings, not the whole problem.
  `save()` writes to `.tmp` and `replace()`s, so a process crash cannot tear the file, but it never `fsync`s before the rename -- a power loss can land the rename ahead of the data.
  `load()` collapses three cases into `{}`: file absent (legitimately empty), file present but unparseable, and `version != SCHEMA_VERSION`.
  The last two must not read as "MANIAC owns nothing", because the next install then treats every managed page as foreign and backs up MANIAC's own work under `--force`.
  There is no backup generation and no reachable repair; `lifecycle._seed_from_headers` is the existing rebuild path, hiding as a migration.
  Repair can recover `path`, `checksum`, `target` and `provider_target` -- ADR-0028 made every entry a symlink, so `readlink` answers the last two -- plus `source` for tier-3 pages from their provenance header.
  It cannot recover `backup`, `version` or `source_uri`, and cannot tell a MANIAC-installed tier-1/2 copy from the vendor's own byte-identical page; ADR-0017 accepted that.
  A symlink into `data_dir` is ownership evidence regardless of header and would cover tiers 1 and 2 post-ADR-0028; check whether a link-target scan replaces `_seed_from_headers`.
  On concurrency, a lock must not span generation.
  LLM synthesis, pandoc and crawling touch nothing the manifest owns; only backup/link/record do, and those are filesystem-fast.
  Split install into generate (unlocked, slow, parallel-safe across processes) and commit (locked, milliseconds), so external parallelism survives.
  The residual cost is two processes generating the same tool and one losing at commit -- wasted LLM spend, not corruption, and the loser can re-read and skip.
  SQLite is not the answer to corruption: atomic rename plus `fsync` already covers torn writes, it still needs an app-level lock across the side-effect window, and it costs inspectability plus a `-wal`/`-shm` pair that breaks naive backups.
- Treat a multi-page upstream release as one uninstallable installation.
  Uninstalling the primary page must checksum-protect, remove, and restore every companion page and displaced vendor page.
  Blocked on the manifest schema above, and a second customer for it.
  `orchestration/install.py` installs a release's pages as one `install_manpage` call each, keyed by `_manpage_owner`, recording no relation between them.
  `source_uri` is the only field companions share -- `release._manpages_from_release_archive` stamps the same asset URL on every extracted page, and the tree probe returns at most one page, so multi-page bundles only ever come from a release archive.
  It still cannot carry the group: it records which upstream file, not that they arrived together, and cannot express which entry is primary, so uninstalling `eza_colors` would look structurally identical to uninstalling `eza`.
  Needs group membership plus primary recorded on `Entry`.

## Refactors and architecture

### List performance

- Design a persistent `maniac list` local-fact cache.
  Cache validated PATH inventory, provider detection, upstream identity, and manpage reachability separately rather than persisting raw rows.
  Guard each record with its explicit filesystem and configuration fingerprints, retain the existing immutable versioned-upstream cache, and benchmark cold and warm listings plus corruption recovery and concurrent readers.
  DiskCache is selected by ADR-0041; do not add `--no-cache` until the fact-cache contract is settled.

### Maintainability

- Decide whether `Config` binds XDG paths per instance or intentionally at import time, then make discovery consistent.
  Current frozen module globals make ordinary environment monkeypatches ineffective after import; this is a configuration-lifecycle decision deserving an ADR.
- Resolve non-GitHub upstreams, or say plainly that they are unsupported.
  `npm`, `pipx`, `uv`, `go`, `homebrew` and `cargo` all discard a repository URL that `discovery._clean_git_url` leaves unchanged, so a GitLab or Codeberg project resolves to nothing even when its metadata declares the URL outright.
  This is one cross-provider policy, not six provider bugs; it pairs with the existing Source-link item, which already refuses to guess a browser-file URL for an unsupported host.
  Supersedes "extend installation-derived package metadata fallback beyond npm": that gap is closed.
  `uv` resolves via `Project-URL` and `direct_url.json`, `pipx` via `find_distribution_metadata`, `go` via module path, `homebrew` via `brew info`, and `cargo` now via the unpacked registry source's declared `repository`.
  Never infer a repository from a bare executable name: prior collisions include `fmt` -> `nushell/nufmt`, `od` -> `todotxt/todo.txt-cli`, and GNU `envsubst` -> `a8m/envsubst`.
- Record losing provider claims for a binary after first-PATH-entry selection.
  The visible winner is correct, but discarded competing claims prevent diagnostics when PATH hides a better-documented installation.
- Tell transparent wrappers from genuinely different shadowing binaries only with evidence: compare `--version` for the first and later PATH occurrences.
  Do not fall through positionally; ADR-0020 rejected that because it can attribute another build's documentation.
  The current system has no live wrapper fixture, so this remains unbuilt.
- Add exact-file Source-link adapters for non-GitHub hosts only when verified against a real installation.
  Keep Source plain otherwise; clone URLs do not imply a safe browser-file URL.

## Features and discovery

- Admit bounded documentation roots in monorepos, with OpenCode's versioned English `packages/web/src/content/docs` as the fixture.
  Exclude dependency, build, translation, and unrelated workspace trees.
- Add package-provenance discovery for system candidates, beginning with batched Debian `dpkg-query` ownership plus source/homepage/copyright evidence.
  Keep downstream package VCS distinct from upstream identity, and defer other package-manager adapters until real installations exist.
- Extract bounded documentation from installed package roots and system packages: recognized local docs, Info pages, package metadata as supplementary context, and absolute-path help.
  Preserve each source's provenance.
- Feed an installed manpage into synthesis as authoritative reference material for flags, defaults, exits, and structure.
  Label it as reference evidence rather than a presentation model, so a poor page cannot anchor the generated result; validate the effect with `maniac compare`.
- Add tldr-pages as an examples source, preferring an installed tealdeer cache before its release zip and recording provenance in generated output.
  Its examples are additive; do not substitute it for authoritative option documentation.
- Consider Arch Wiki integration/configuration prose only after a reliable per-command extraction boundary exists.
  It is additive, but task-oriented articles and redirects make raw retrieval insufficient.
- Split the external `system` Source label into package-owned and other external pages, or rename it.
  A conservative roff-header verifier may establish freshness only on unambiguous title/package/version evidence.
- Add external-page freshness adapters for Arch, RPM-family systems, and Homebrew only with a real fixture.
  Unsupported systems must remain `unverified`, never guessed.
- Implement an update path for stale MANIAC-managed pages, including whether installation overwrites in place and when a vendor backup is retaken.
  Re-derive pre-version manifest entries by reinstalling rather than stamping current versions; make the repair resumable.
- Add a dense-table row guide only if it improves tested light and dark terminal output.
  A theme-safe alternating row style is the leading candidate.

## Verification and research

- Verify `maniac compare` against a live LLM and a real installed page; its current coverage mocks synthesis.
  The staged fzf context is ready when API access is available.
- Measure the coverage cost of refusing an unmatched upstream version before changing ADR-0016's version-match policy.
  Separate unusual tag conventions from genuine absence.
- Exercise standalone npm, pipx, and Homebrew `detect()` implementations against real installations.
  Existing mise-composed metadata coverage does not verify their path layouts.

## Deferred milestones

- Judge whether an existing vendor or shipped upstream manpage is worth replacing.
  ADR-0016 deliberately deferred the verdict layer; resume only with a robust classification model and real output to judge.
  `.HP` option counting has two known dead ends: unconditional counts mistake synopsis markers for options, and rendered bold-hyphen regexes miss common roff forms.
- Serve system and distro-packaged binaries.
  Debian provenance and local documentation are viable, but broad candidate enumeration and cross-package-manager support need batching and measured scope.

## Settled exclusions

- Do not use linuxcommandlibrary.com: its generated, lossy, weakly attributed corpus is not a trustworthy documentation source.
- Do not fetch man7.org, Debian/Ubuntu manpage mirrors, or GNU online manuals where local pages and Info already supply the same content.
  Install `manpages-dev` for missing local sections instead.
