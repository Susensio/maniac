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
- Replace `installer.install_manpage`'s and `manifest.record`'s twelve-parameter signatures with one entry record.
  Both describe the same installed page and drifted into parallel positional lists; ADR-0034 moved their coordination but not their shape.
  Both still trip the raised `max-args = 10`, which is the point of that threshold: twelve parameters is coordination, seven is a command surface.
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
- Run the structural link scan on every manifest load, not only on transaction open.
  ADR-0046 built it on transaction open, which is write paths only -- so `maniac list` still never notices drift, and the scan runs when you install rather than when you look.
  The reason given was ADR-0034's load purity, but that conflates two things: promotion mutates and cannot live in `load`, while the scan is `lstat`/`readlink` only and mutates nothing.
  Measured at ~0.046 ms/entry against an 11.8 s `maniac list`, and it walks what MANIAC owns (2 entries here), not what is on `$PATH` (68 rows).
  Divergence is the common failure, corruption the rare one: a page deleted by hand, `output_dir` or `backup_dir` cleaned, another user-level installer writing into `~/.local/share/man/man1`.
  Note the seam with the list fact cache, whose reachability records take the manifest as an input.
- Repair an ADR-0032 migration interrupted between relinking and its manifest write.
  `BUG:` marked at `maniac/lifecycle.py:276`.
  The eligibility guard skips the entry forever and uninstall reports it MODIFIED; found by a Codex review of ADR-0046's work, whose four other findings were fixed.
- Give a reconstructed entry an honest tier.
  ADR-0046's recovery stamps `Tier.SYNTHESIS` with `source="reconstructed"`; the source is true and the tier is a guess, because `output_dir` holds tier-2 and tier-3 pages alike with nothing on disk separating them.
  Either find evidence that separates them or admit an unknown tier.
- Reconstruct tier-1 direct provider links, or accept losing them.
  ADR-0046 refused: "not under `output_dir`" is not evidence of a provider root, and adopting one would let MANIAC replace and later remove a symlink the user owns.
  The cost is that a fully lost manifest loses tier-1 ownership entirely. Real evidence would be a target resolving beneath a live provider install root, which `sources.candidates` can already establish.
- Correct `modified_kept`'s message for dangling and retargeted entries.
  It says "its bytes have changed since"; nothing was edited in either case.
  Same defect class as the `legacy_kept` split already made, one level down.
- Decide whether uninstalling a companion page removes its whole group.
  ADR-0042 shipped symmetric removal: uninstalling any member takes the unit, so `maniac uninstall eza_colors` removes `eza.1` too.
  That is defensible -- they are one installation, and leaving the primary without its companions is the half-installed state grouping exists to prevent -- but it deletes a page the user did not name, which is the surprising half.
  The asymmetry worth weighing: nobody installs `eza_colors`.
  It is a manifest key derived from a page filename that arrived with `maniac install eza`, so typing `maniac uninstall eza_colors` is already a confused command, and answering it by silently removing `eza` teaches the wrong model.
  Candidates: keep symmetric removal; refuse and redirect ("`eza_colors` is part of `eza`'s installation; run `maniac uninstall eza`"), which teaches the grouping; or remove symmetrically but report the full set first and confirm, which is what `apt` does for a dependency.
  Refusing has a cost worth naming: a user whose primary entry is already gone would have no way to remove an orphaned companion, so whichever wins needs an escape hatch.
- Prune a release group when its upstream drops a page.
  `Entry.group` (ADR-0042) records membership at install time and install has no pruning pass, so a member that a later release no longer ships stays recorded.
  Uninstall then looks for a page that upstream stopped shipping.

- Invalidate the process-local provider memoization that hides a mid-run filesystem change.
  `providers/uv.py`'s `_local_editable_dir` and `_installed_version` are `functools.cache`d by install root, and `providers/pipx.py`'s `find_distribution_metadata` is `functools.cache`d by `(root, package)`, neither with any invalidation.
  Reproduced on 2026-09-15: a uv editable checkout appearing after an earlier lookup still reads `None`, and a `METADATA` version rewritten between two calls for one root still reads the first value.
  Within a single `list` run the inputs rarely change, so this is latent rather than observed in normal use; it matters for a long-lived process and for any caller that installs and then re-reads.
  The abandoned fact-cache branch neutralized both with an evidence-driven `clear_source_cache()` call, so dropping that branch leaves this unaddressed; a fix here needs its own invalidation boundary rather than that machinery.

## Refactors and architecture
- Roll back earlier pages when a grouped reinstall fails partway.
  `BUG:` at `maniac/orchestration/install.py:211`, found by Codex review of ADR-0046's work and reproduced.
  ADR-0046's one-transaction boundary means no page is *recorded* when a later one fails, and ADR-0046's orphan adoption recovers a first-time install's stray symlink and backup -- that half is by design, not a defect.
  A reinstall is the gap: the entry already exists carrying the old checksum while the durable target now holds the new bytes, so uninstall reports MODIFIED.
  Adoption only builds entries that are missing; it never corrects one that is present and wrong.
  Needs a cross-page filesystem undo log, which is a new mechanism and an ADR-level decision -- weigh it against simply re-verifying checksums against disk on the next transaction, which the link scan already has the shape for.
- Install every page of a multi-page install-root release, not just the primary.
  `_try_install_root` uses `candidate.final_target` alone and ignores `candidate.pages`, so a tier-1 release ships its primary with no `group` recorded.
  Tier 2 installs the whole bundle as one unit (ADR-0042, ADR-0046); tier 1 does not, and nothing says why.
  A gap rather than a regression -- it predates the grouping work.

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
- Do not change what `maniac list` prints when stdout is not a terminal.
  It renders the Tool column alone, because Rich falls back to 80 columns and drops the other three.
  Decided 2026-09-15: bare names are the wanted pipe output, so the accident and the intent coincide.
  A plain-text width, a `--plain` mode and `--json` were all considered and are not wanted now; revisit only if a real consumer needs the other columns.

- Do not use linuxcommandlibrary.com: its generated, lossy, weakly attributed corpus is not a trustworthy documentation source.
- Do not fetch man7.org, Debian/Ubuntu manpage mirrors, or GNU online manuals where local pages and Info already supply the same content.
  Install `manpages-dev` for missing local sections instead.
