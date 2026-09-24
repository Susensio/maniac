# Backlog

Open work nobody has committed to.
Each entry names something someone could start, or what would unblock it.
Settled decisions live in `docs/adr/`; defects with a line to sit beside are marked there, and `rg -n 'BUG:|TODO:'` lists them.

## Bugs and correctness

- Distinguish a wrong documentation repository from one that legitimately has no manpage.
  Flag-inventory overlap and whether the repository contains implementation source are possible evidence, but absence is a normal synthesis fallback and must not be treated as proof of misresolution.
- Drop Mise-activated `$PATH` entries on a degraded login-path fallback, as venv and conda entries already are.
  Every `login_path()` fallback already returns a sanitized `LoginPath(path, degraded)`, and `MISE_`/`__MISE_` are scrubbed.
  Removal needs evidence: only `VIRTUAL_ENV` and `CONDA_PREFIX` name a root a `$PATH` entry can be tested against, and no Mise variable names a per-tool root.
  `__MISE_ORIG_PATH` records the whole pre-activation `$PATH` and is the plausible evidence source.
  Next step: decide whether the fallback gets per-provider evidence adapters or stays generic.
  Shim resolution stays unexercised until a populated Mise shim directory exists; `mise which -C $HOME` is cwd-sensitive and needs ADR-0029-style root validation.

## Refactors and architecture

- Install every page of a multi-page install-root release, not just the primary.
  `_try_install_root` uses `candidate.final_target` alone and ignores `candidate.pages`, so a tier-1 release ships its primary with no `group` recorded.
  Tier 2 installs the whole bundle as one unit (ADR-0042, ADR-0046); tier 1 does not, and nothing says why.
- Decide whether `Entry` splits into a descriptive draft and a written record.
  `path` and `checksum` are fields no caller can know at call time, so `draft_entry` fills them with inert placeholders (`Path()`, `""`) that `install_manpage` overwrites before anything reads them.
  A wart rather than a defect: making every call site resolve the destination itself would move coordination outward instead of removing it.
  Reopening `Entry` touches ADR-0046's manifest boundary, so it starts with an ADR.
- Record losing provider claims for a binary after first-PATH-entry selection.
  The visible winner is correct, but discarded competing claims prevent diagnostics when PATH hides a better-documented installation.
- Tell transparent wrappers from genuinely different shadowing binaries by comparing `--version` for the first and later PATH occurrences.
  Never fall through positionally (ADR-0020).
  Blocked on a live wrapper fixture.
- Add exact-file Source-link adapters for non-GitHub hosts.
  Blocked on a real installation to verify against; clone URLs do not imply a safe browser-file URL.

## Features and discovery

- Serve system and distro-packaged binaries, starting with Debian package provenance for system candidates.
  `maniac/sources/packages.py` already asks `dpkg-query` for a page's owner, version and `${Source}`; what is missing is batched ownership for binaries plus homepage/copyright evidence for upstream identity.
  Keep downstream package VCS distinct from upstream identity, and defer other package managers until real installations exist.
- Extract bounded documentation from installed package roots and system packages: recognized local docs, Info pages, package metadata as supplementary context, and absolute-path help.
  Preserve each source's provenance.
- Add tldr-pages as an examples source, preferring an installed tealdeer cache before its release zip and recording provenance in generated output.
  Its examples are additive; do not substitute it for authoritative option documentation.
- Consider Arch Wiki integration/configuration prose.
  Blocked on a reliable per-command extraction boundary: task-oriented articles and redirects make raw retrieval insufficient.
- Add external-page freshness adapters for Arch, RPM-family systems and Homebrew, for pages whose `.TH`/`.Dt` header carries no version.
  Blocked on a real fixture per system; unsupported systems stay `unverified`, never guessed.
- Implement an update path for stale MANIAC-managed pages, including whether installation overwrites in place and when a vendor backup is retaken.
  Re-derive pre-version manifest entries by reinstalling rather than stamping current versions; make the repair resumable.
- Pick up shell completions alongside manpages.
  Next step: decide scope and shape.
  The discovery problem transfers -- does the tool ship its own, can one be generated, is it reachable -- and many CLIs expose a completions subcommand the way others ship a manpage.
  The installation surface does not: completions have one convention per shell (bash-completion, zsh `fpath`, fish `completions/`), so this is closer to a second product than an extension of `install_manpage`.
- Judge whether an existing vendor or shipped upstream manpage is worth replacing.
  ADR-0016 deferred the verdict layer; blocked on a robust classification model and real output to judge.
  `.HP` option counting has two known dead ends: unconditional counts mistake synopsis markers for options, and rendered bold-hyphen regexes miss common roff forms.

## Verification and research

- Verify `maniac compare` against a live LLM and a real installed page; its current coverage mocks synthesis.
  Blocked on API access; the staged fzf context is ready.
- Measure the coverage cost of refusing an unmatched upstream version before changing ADR-0016's version-match policy.
  Separate unusual tag conventions from genuine absence.
- Exercise standalone npm, pipx and Homebrew `detect()` implementations against real installations.
  Blocked on those installations; mise-composed metadata coverage does not verify their path layouts.
