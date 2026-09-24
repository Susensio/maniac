# Backlog

Open work with no single line to mark.
`rg -n 'BUG:|TODO:'` lists the rest.

## Next round

Yielded by the 2026-09-14 architecture wave (ADR-0033, ADR-0034, ADR-0035).
These are findings the work surfaced and deliberately did not take; they are first, not filed.

### Structure

- Decide whether `Entry` splits into a descriptive draft and a written record.
  Surfaced by collapsing `install_manpage`'s parameter list into an `Entry` (`62b6881`, 13 arguments to 8).
  `path` and `checksum` are `Entry` fields no caller can know at call time -- `install_manpage` resolves the destination and hashes the file itself -- so `draft_entry` fills them with inert placeholders (`Path()`, `""`) that `dataclasses.replace` overwrites before anything reads or stores them.
  Documented and functionally inert, so this is a wart rather than a defect, and the alternative was worse: making every call site duplicate that resolution moves coordination outward instead of removing it.
  The cost is that one type now serves two roles, with two fields meaningless in the first.
  Reopening `Entry` touches ADR-0046's manifest boundary, so it is an ADR-level decision, not a mechanical follow-up.

## Bugs and correctness

- Distinguish a wrong documentation repository from one that legitimately has no manpage.
  Flag-inventory overlap and whether the repository contains implementation source are possible evidence, but absence is a normal synthesis fallback and must not be treated as proof of misresolution.
- Drop Mise-activated `$PATH` entries on a degraded login-path fallback, as venv and conda entries already are.
  The generic half is done: every `login_path()` fallback now returns a sanitized `LoginPath(path, degraded)` rather than the caller's raw `$PATH`, and `MISE_`/`__MISE_` joined the scrubbed prefixes.
  Removal needs evidence, and only `VIRTUAL_ENV` and `CONDA_PREFIX` name a root a `$PATH` entry can be tested against.
  No Mise variable names a per-tool root; `~/.local/share/mise/installs/...` would have to be reconstructed by convention, which is the guessing this rule exists to forbid.
  `__MISE_ORIG_PATH` records the entire pre-activation `$PATH` and is the plausible evidence source -- a live `mise activate bash` on this machine exports it alongside `MISE_SHELL`, `__MISE_EXE` and `__MISE_DIFF`.
  Using it is Mise-specific, so decide whether the fallback gets per-provider evidence adapters or stays generic.
  Shim resolution remains unexercised: this machine has no populated shim directory, and `mise which -C $HOME` is cwd-sensitive and needs ADR-0029-style root validation.

## Refactors and architecture
- Install every page of a multi-page install-root release, not just the primary.
  `_try_install_root` uses `candidate.final_target` alone and ignores `candidate.pages`, so a tier-1 release ships its primary with no `group` recorded.
  Tier 2 installs the whole bundle as one unit (ADR-0042, ADR-0046); tier 1 does not, and nothing says why.
  A gap rather than a regression -- it predates the grouping work.

### Maintainability

- Record losing provider claims for a binary after first-PATH-entry selection.
  The visible winner is correct, but discarded competing claims prevent diagnostics when PATH hides a better-documented installation.
- Tell transparent wrappers from genuinely different shadowing binaries only with evidence: compare `--version` for the first and later PATH occurrences.
  Do not fall through positionally; ADR-0020 rejected that because it can attribute another build's documentation.
  The current system has no live wrapper fixture, so this remains unbuilt.
- Add exact-file Source-link adapters for non-GitHub hosts only when verified against a real installation.
  Keep Source plain otherwise; clone URLs do not imply a safe browser-file URL.

## Features and discovery

- Add package-provenance discovery for system candidates, beginning with batched Debian `dpkg-query` ownership plus source/homepage/copyright evidence.
  Keep downstream package VCS distinct from upstream identity, and defer other package-manager adapters until real installations exist.
- Extract bounded documentation from installed package roots and system packages: recognized local docs, Info pages, package metadata as supplementary context, and absolute-path help.
  Preserve each source's provenance.
- Add tldr-pages as an examples source, preferring an installed tealdeer cache before its release zip and recording provenance in generated output.
  Its examples are additive; do not substitute it for authoritative option documentation.
- Consider Arch Wiki integration/configuration prose only after a reliable per-command extraction boundary exists.
  It is additive, but task-oriented articles and redirects make raw retrieval insufficient.
- Add external-page freshness adapters for Arch, RPM-family systems, and Homebrew only with a real fixture.
  Unsupported systems must remain `unverified`, never guessed.
  Narrowed by `maniac/sources/roff.py` (`0518507`, 2026-09-21): a page's own `.TH`/`.Dt` header now proves freshness on any system when it carries a parseable version, no package manager involved, so this item is now specifically about pages whose header carries no version at all and therefore need a native package-manager fact to fall back on -- not about Arch/RPM/Homebrew support in general.
- Implement an update path for stale MANIAC-managed pages, including whether installation overwrites in place and when a vendor backup is retaken.
  Re-derive pre-version manifest entries by reinstalling rather than stamping current versions; make the repair resumable.

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
- Pick up shell completions alongside manpages.
  Suggested 2026-09-21; scope and shape not decided, captured so it is not lost.
  The same discovery problem MANIAC already solves for manpages -- does the tool ship its own, can one be generated, is it reachable from where the shell looks -- applies to completions too, and many CLIs already expose a `completions`/`--generate-completion` subcommand the way others ship a manpage in their install tree.
  The installation surface does not transfer directly, though: manpages have one convention (`MANPATH`), completions have three, one per shell (`~/.local/share/bash-completion/completions/`, zsh's `fpath`, fish's `~/.config/fish/completions/`), so this is closer in shape to a second product than an extension of `install_manpage`.
