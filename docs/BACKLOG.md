# Backlog

Open work with no single line to mark.
`rg -n 'BUG:|TODO:'` lists the rest.

## Next round

Yielded by the 2026-09-14 architecture wave (ADR-0033, ADR-0034, ADR-0035).
These are findings the work surfaced and deliberately did not take; they are first, not filed.

### Correctness

- Report the superseded durable copy that an `INSTALL_ROOT` uninstall deletes.
  Uninstall's own `reconcile()` runs ADR-0032's migration first, which relinks the entry to its provider page, sets `provider_target`, and unlinks the MANIAC copy itself.
  That deletion never reaches `result.removed`, so uninstall removes a file it does not report.
  Found while pinning `discard_durable_target`; the removal is correct, the silence is not.
- Assert `lifecycle.link_manpath_entry`'s atomic replacement, not just its end state.
  Current tests pin that the manpath entry ends as the right symlink; nothing pins that a pre-existing entry is replaced atomically rather than unlinked and recreated.
  Needs an interleaving harness the project does not yet have.

### Structure

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
- Harden global Mise discovery when the login-path probe fails, rather than returning the caller's inherited, possibly project-activated PATH.
  A disposable probe confirmed activation is cwd-sensitive; the successful login-shell path run from `$HOME` correctly selects global `latest` installs.
  Exercise real shim resolution before choosing a safe fallback; `mise which -C $HOME` remains cwd-sensitive and needs ADR-0029-style root validation.
- Serialize manifest load-modify-save with a sidecar lock and transactional update API.
  Atomic replacement prevents torn files but not concurrent installs losing ownership entries; choose a short blocking timeout or a fail-fast lock policy.
- Disambiguate list rows whose grouping renders the same visible label for different binaries or states.
  Preserve bare-name pipeline output; choose either package-plus-binary labels or a width-capped binary list.
- Treat a multi-page upstream release as one uninstallable installation.
  Uninstalling the primary page must checksum-protect, remove, and restore every companion page and displaced vendor page.

## Refactors and architecture

### Architecture review follow-up

- Centralize verified source selection in one candidate service carrying tier, pages, provenance URI, version match, and target ownership.
  Install, listing, and manifest reconciliation must consume that result rather than each reimplementing root containment and repository eligibility.
- Defer upstream identity resolution in `list` until local evidence leaves it necessary, as ADR-0025 requires.
  Reachable and vendor-page rows should not pay provider or registry resolution merely to render a row.
- Thread one resolved-tool context through all install tiers, including tier 3, rather than resolving the installation and repository again inside `run_pipeline`.
  Carry the selected binary, installation, provider, installed version, and canonical documentation source from the first resolution through authoritative installation and synthesis.
  Tier 3 must be able to consume those facts directly when `install` already found them, while retaining one explicit entry path for direct synthesis.
  This removes the duplicate `discover_repo()` and `find_installation()` work in `run_pipeline`, keeps version-match evidence local to one flow, and shrinks its current configuration-heavy interface.
- Separate listing inventory/classification and probe scheduling from Rich rendering and Typer command wiring.
  Keep a non-CLI inventory service that owns candidate enumeration, local classification, bounded upstream probes, deduplication, and ordered row snapshots.
  Keep Rich rendering and the Typer command as thin adapters over that inventory interface, so progress callbacks and terminal refresh timing cannot steer classification facts.
  Preserve ADR-0024's stable streaming rows and ADR-0025's local-first upstream policy as contracts tested at the inventory seam; terminal tests should cover only rendering behavior.
- Replace correlated `RepoSource` string fields with validated local and remote source variants carrying canonical identity and clone data.

### Maintainability

- Decide whether `Config` binds XDG paths per instance or intentionally at import time, then make discovery consistent.
  Current frozen module globals make ordinary environment monkeypatches ineffective after import; this is a configuration-lifecycle decision deserving an ADR.
- Extend installation-derived package metadata fallback beyond npm for Python, Cargo, Go, and Homebrew.
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
