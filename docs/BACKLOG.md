# Backlog

Open work nobody has committed to.
Each entry names something someone could start, or what would unblock it.
Settled decisions live in `docs/adr/`; defects with a line to sit beside are marked there, and `rg -n 'BUG:|TODO:'` lists them.

## Bugs and correctness

- Distinguish a wrong documentation repository from one that legitimately has no manpage.
  Flag-inventory overlap and whether the repository contains implementation source are possible evidence, but absence is a normal synthesis fallback and must not be treated as proof of misresolution.

- Recognise Mise shims as a provider claim.
  ADR-0062's conventional Mise setup (`mise activate --shims` in the login profile) puts `~/.local/share/mise/shims/` first on the login `$PATH`, and no provider claims a shim, so a shimmed tool resolves as unclaimed and falls to tier-3 synthesis instead of tiers 1-2.
  Next step: resolve a shim to the install it runs from `$HOME` (`mise which <tool>`, with the same activation scrub as `_run_mise`) and claim that, keeping the global-selection check (ADR-0061).
  Shims dispatch per cwd, so the resolved install, not the shim, is what `--help` and `--version` must run.

## Refactors and architecture

- Surface `Installation.losers` to the user.
  Losing later-`$PATH` claims are retained on the model but reach no output: `maniac list` rows (`ToolRow`) carry no `Installation`, and there is no diagnostic command.
  Next step: decide the surface -- a `list` column/flag or a new diagnostic command.
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

- Exercise real Pandoc and Groff compilation in CI, not only mocked unit paths.
  Install both tools in GitHub Actions and add a dedicated integration check that requires them, while unit tests retain their deterministic stubs.
- Verify `maniac compare` against a live LLM and a real installed page; its current coverage mocks synthesis.
  Blocked on API access; the staged fzf context is ready.
- Measure the coverage cost of refusing an unmatched upstream version before changing ADR-0016's version-match policy.
  Separate unusual tag conventions from genuine absence.
- Exercise standalone npm, pipx and Homebrew `detect()` implementations against real installations.
  Blocked on those installations; mise-composed metadata coverage does not verify their path layouts.
