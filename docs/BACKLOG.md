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

- Surface `Installation.losers` to the user.
  Losing later-`$PATH` claims are retained on the model but reach no output: `maniac list` rows (`ToolRow`) carry no `Installation`, and there is no diagnostic command.
  Next step: decide the surface -- a `list` column/flag or a new diagnostic command.
- Retain competing providers claiming the same `bin_path` at one `$PATH` entry.
  `_detect_via_registry` stops at the first matching provider, and it is also the single-name lookup path for `find_installation`/`discover_repo`, so collecting all claims changes that hot path's cost.
- Link tier-1 companion pages from the install root instead of copying them.
  `_try_install_root` copies every non-primary page into `output_dir` because only `final_target`'s containment is verified (`candidate.provider_owned`); a per-page containment check on `InstallRootCandidate` would let companions link like the primary.
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
