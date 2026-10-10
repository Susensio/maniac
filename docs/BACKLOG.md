# Backlog

Open work nobody has committed to.
Each entry names something someone could start, or what would unblock it.
Settled decisions live in `docs/adr/`; defects with a line to sit beside are marked there, and `rg -n 'BUG:|TODO:'` lists them.

## Bugs and correctness

- Distinguish a wrong documentation repository from one that legitimately has no manpage.
  Flag-inventory overlap and whether the repository contains implementation source are possible evidence, but absence is a normal synthesis fallback and must not be treated as proof of misresolution.

## Refactors and architecture

- Retain competing providers claiming the same `bin_path` at one `$PATH` entry.
  `_detect_via_registry` stops at the first matching provider, and it is also the single-name lookup path for `find_installation`/`discover_repo`, so collecting all claims changes that hot path's cost.
- Cut `maniac list` below ~10s warm (measured 2026-09-24 at 0ae53ad).
  No single hotspot remains: inside the ~9s local-classify pool, `man -w` (80 calls) ~2.9s, provider `local_docs` ~2.5s and `resolve_source` ~2.3s wall-union; startup ~1s.
  Measure thread-aware (wall-clock union across the pool), never by summing per-call durations or plain cProfile -- both misled this session.
- Link tier-1 companion pages from the install root instead of copying them.
  `_try_install_root` copies every non-primary page into `output_dir` because only `final_target`'s containment is verified (`candidate.provider_owned`); a per-page containment check on `InstallRootCandidate` would let companions link like the primary.
- Tell transparent wrappers from genuinely different shadowing binaries by comparing `--version` for the first and later PATH occurrences.
  Never fall through positionally (ADR-0020).
  Blocked on a live wrapper fixture.
- Add exact-file Source-link adapters for non-GitHub hosts.
  Blocked on a real installation to verify against; clone URLs do not imply a safe browser-file URL.

## Features and discovery

- Seed synthesis with an outdated, human-written page of the same software.
  A page documenting another version carries structure, prose and examples that `--help` lacks: a losing `$PATH` copy's own page (`Installation.losers`, retained for this and shown nowhere), an upstream page at a non-matching tag (refused by tier 2, ADR-0016), a page maniac replaced (its backup).
  Give it to the model labelled with the version it documents; the current `--help` wins wherever they disagree, and options it no longer lists are dropped.
  Only human-written pages qualify: one maniac synthesized (provenance signature, `Tier.SYNTHESIS`) or help2man generated (`is_help2man_content`) is derived from the same help and docs the model already gets, so it is no better a seed than none and would carry the last model's errors forward.
  A loser's page needs proof of the same software first -- the same package or repository as the winner, as ADR-0055 requires -- since a shared name is not evidence.
  Next step: decide how the seed is recorded in provenance; the recorded version stays the current binary's under ADR-0019.
- Support setups that build `$PATH` outside the login profile.
  ADR-0062 assumes a conventional setup; a valid one that exports `$PATH` only from an interactive rc file (Mise's front-page `mise activate` in `.bashrc`, behind Debian's interactive guard) has its global tools silently missing from the login `$PATH`.
  Next step: find evidence that tells such a setup apart from a machine without those tools (e.g. a provider's global install root existing while nothing on the login `$PATH` reaches it), then decide whether to fall back (an `-ilc` read is unsafe: rc files may `exec`) or to report it with the fix.
- Extract bounded documentation from installed package roots (system packages are out of scope, ADR-0059): recognized local docs, Info pages, package metadata as supplementary context, and absolute-path help.
  Preserve each source's provenance.
- Add tldr-pages as an examples source, preferring an installed tealdeer cache before its release zip and recording provenance in generated output.
  Its examples are additive; do not substitute it for authoritative option documentation.
- Consider Arch Wiki integration/configuration prose.
  Blocked on a reliable per-command extraction boundary: task-oriented articles and redirects make raw retrieval insufficient.
- Add external-page freshness adapters for Arch, RPM-family systems and Homebrew, for pages whose `.TH`/`.Dt` header carries no version.
  Blocked on a real fixture per system; unsupported systems stay `unknown`, never guessed.
- Pick up shell completions alongside manpages.
  Next step: decide scope and shape.
  The discovery problem transfers -- does the tool ship its own, can one be generated, is it reachable -- and many CLIs expose a completions subcommand the way others ship a manpage.
  The installation surface does not: completions have one convention per shell (bash-completion, zsh `fpath`, fish `completions/`), so this is closer to a second product than an extension of `install_manpage`.
- Judge whether an existing `shipped` or `system` page is worth replacing.
  ADR-0016 deferred the verdict layer; blocked on a robust classification model and real output to judge.
  `.HP` option counting has two known dead ends: unconditional counts mistake synopsis markers for options, and rendered bold-hyphen regexes miss common roff forms.

## Verification and research

- Verify `maniac compare` against a live LLM and a real installed page; its current coverage mocks synthesis.
  Blocked on API access; the staged fzf context is ready.
- Measure the coverage cost of refusing an unmatched upstream version before changing ADR-0016's version-match policy.
  Separate unusual tag conventions from genuine absence.
- Exercise the standalone Homebrew `detect()` against a real installation.
  Mise-composed metadata coverage does not verify its path layout; npm and pipx are covered by `tests/integration/test_providers.py`.
  Blocked on a real Homebrew installation.
