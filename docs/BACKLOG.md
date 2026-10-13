# Backlog

Open work nobody has committed to.
Each entry names something someone could start, or what would unblock it.
Settled decisions live in `docs/adr/`; defects with a line to sit beside are marked there, and `rg -n 'BUG:|TODO:'` lists them.

## Bugs and correctness

- Distinguish a wrong documentation repository from one that legitimately has no manpage.
  Flag-inventory overlap and whether the repository contains implementation source are possible evidence, but absence is a normal synthesis fallback and must not be treated as proof of misresolution.

## Refactors and architecture

- Retain competing providers claiming the same `bin_path` at one `$PATH` entry.
  `_detect_via_registry` stops at the first matching provider, and it is also the single-name lookup path behind `resolution.locate`, so collecting all claims changes that hot path's cost.
- Cut `maniac scan` below ~10s warm.
  Re-measure first: the profile on record (2026-09-24, at 0ae53ad) predates the redesign, when discovery was `list`; it found no single hotspot (`man -w`, provider `local_docs` and `resolve_source` each ~2-3s of wall-union inside the local-classify pool, startup ~1s).
  Measure thread-aware (wall-clock union across the pool), never by summing per-call durations or plain cProfile -- both misled that session.

## Features and discovery

- Seed generation from two more pages of the same software (`candidates.seed_for` takes the first it finds).
  A losing `$PATH` copy's shipped page (`Installation.losers`), once proven the same software by package or repository (ADR-0055); `locate` does not collect losers yet, only `locate_all` does.
  The page `install --force` replaced (its backup), if something beyond the shared name ties it to the tool.
- Extract bounded documentation from installed package roots (system packages are out of scope, ADR-0059): recognized local docs, Info pages, package metadata as supplementary context, and absolute-path help.
  Preserve each source's provenance.
- Add tldr-pages as an examples source, preferring an installed tealdeer cache before its release zip and recording provenance in generated output.
  Its examples are additive; do not substitute it for authoritative option documentation.
- Completions beyond ADR-0066's first cut.
  For a tool maniac installs no page for (today they go with a page, so `list`, `update` and `remove` see every one); recovered from their links when the manifest is damaged, as pages are; counted by `scan`; for shells beyond bash, zsh and fish (nu, PowerShell, elvish).

## Verification and research

- Measure seeding: `maniac dev bench` and `maniac dev bench --no-seed` on tools that have a seed (`maniac why <tool>` names it), then compare the judge's scores side by side in `results.json`.
  Needs an API key; the default tools (howdoi, hx, uv) may well have no seed, so pick ones that do.
- Verify `maniac dev eval --against-installed` against a live LLM and a real installed page; its tests mock synthesis.
  Runs wherever an API key is configured (as `maniac dev bench` does); the staged fzf context is ready.
- Measure the coverage cost of refusing an unmatched upstream version before changing ADR-0016's version-match policy.
  Separate unusual tag conventions from genuine absence.

## Parked

Waiting on a fixture, an installation or an idea nobody has; each says what would unblock it.
Move one back up when that turns up.

- Tell transparent wrappers from genuinely different shadowing binaries by comparing `--version` for the first and later PATH occurrences.
  Never fall through positionally (ADR-0020).
  Blocked on a live wrapper fixture.
- Add exact-file Source-link adapters for non-GitHub hosts.
  Blocked on a real installation to verify against; clone URLs do not imply a safe browser-file URL.
- Consider Arch Wiki integration/configuration prose.
  Blocked on a reliable per-command extraction boundary: task-oriented articles and redirects make raw retrieval insufficient.
- Add external-page freshness adapters for Arch, RPM-family systems and Homebrew, for pages whose `.TH`/`.Dt` header carries no version.
  Blocked on a real fixture per system; unsupported systems stay `unknown`, never guessed.
- Judge whether an existing `shipped` or `system` page is worth replacing.
  ADR-0016 deferred the verdict layer; blocked on a robust classification model and real output to judge.
  `.HP` option counting has two known dead ends: unconditional counts mistake synopsis markers for options, and rendered bold-hyphen regexes miss common roff forms.
- Exercise the standalone Homebrew `detect()` against a real installation.
  Mise-composed metadata coverage does not verify its path layout; npm and pipx are covered by `tests/integration/test_providers.py`.
  Blocked on a real Homebrew installation.
