# Backlog

Open work with no single line to mark.
`rg -n 'BUG:|TODO:'` lists the rest.

## Next round

Yielded by the 2026-09-14 architecture wave (ADR-0033, ADR-0034, ADR-0035).
These are findings the work surfaced and deliberately did not take; they are first, not filed.

### Correctness

- Assert `lifecycle.link_manpath_entry`'s atomic replacement, not just its end state.
  Current tests pin that the manpath entry ends as the right symlink; nothing pins that a pre-existing entry is replaced atomically rather than unlinked and recreated.
  Needs an interleaving harness the project does not yet have.

### Structure

- Thread probe definitiveness back as a return value instead of `docs.cache`'s module-level `_lookup_state` thread-local.
  ADR-0033's split made that cross-module channel visible without removing it: `cache` writes it and `repository` reads it.
  Removing it touches every probe signature, so it was left out of the split deliberately.
- Decide whether `Entry` splits into a descriptive draft and a written record.
  Surfaced by collapsing `install_manpage`'s parameter list into an `Entry` (`62b6881`, 13 arguments to 8).
  `path` and `checksum` are `Entry` fields no caller can know at call time -- `install_manpage` resolves the destination and hashes the file itself -- so `draft_entry` fills them with inert placeholders (`Path()`, `""`) that `dataclasses.replace` overwrites before anything reads or stores them.
  Documented and functionally inert, so this is a wart rather than a defect, and the alternative was worse: making every call site duplicate that resolution moves coordination outward instead of removing it.
  The cost is that one type now serves two roles, with two fields meaningless in the first.
  Reopening `Entry` touches ADR-0046's manifest boundary, so it is an ADR-level decision, not a mechanical follow-up.

## Bugs and correctness

- Distinguish a definitive tier-2 absence from a transient repository probe failure.
  The latter must not silently fall through to synthesis, which can conceal a wrong repository or a network, tag, tree, release, or validation failure.
  Report the consulted repository and tier immediately; then decide between interactive confirmation and uniform refusal while preserving ordinary synthesis fallback and the explicit `--no-synthesize` opt-out.
- Distinguish a wrong documentation repository from one that legitimately has no manpage.
  Flag-inventory overlap and whether the repository contains implementation source are possible evidence, but absence is a normal synthesis fallback and must not be treated as proof of misresolution.
- Drop Mise-activated `$PATH` entries on a degraded login-path fallback, as venv and conda entries already are.
  The generic half is done: every `login_path()` fallback now returns a sanitized `LoginPath(path, degraded)` rather than the caller's raw `$PATH`, and `MISE_`/`__MISE_` joined the scrubbed prefixes.
  Removal needs evidence, and only `VIRTUAL_ENV` and `CONDA_PREFIX` name a root a `$PATH` entry can be tested against.
  No Mise variable names a per-tool root; `~/.local/share/mise/installs/...` would have to be reconstructed by convention, which is the guessing this rule exists to forbid.
  `__MISE_ORIG_PATH` records the entire pre-activation `$PATH` and is the plausible evidence source -- a live `mise activate bash` on this machine exports it alongside `MISE_SHELL`, `__MISE_EXE` and `__MISE_DIFF`.
  Using it is Mise-specific, so decide whether the fallback gets per-provider evidence adapters or stays generic.
  Shim resolution remains unexercised: this machine has no populated shim directory, and `mise which -C $HOME` is cwd-sensitive and needs ADR-0029-style root validation.

- Invalidate the process-local provider memoization that hides a mid-run filesystem change.
  `providers/uv.py`'s `_local_editable_dir` and `_installed_version` are `functools.cache`d by install root, and `providers/pipx.py`'s `find_distribution_metadata` is `functools.cache`d by `(root, package)`, neither with any invalidation.
  Reproduced on 2026-09-15: a uv editable checkout appearing after an earlier lookup still reads `None`, and a `METADATA` version rewritten between two calls for one root still reads the first value.
  Within a single `list` run the inputs rarely change, so this is latent rather than observed in normal use; it matters for a long-lived process and for any caller that installs and then re-reads.
  The abandoned fact-cache branch neutralized both with an evidence-driven `clear_source_cache()` call, so dropping that branch leaves this unaddressed; a fix here needs its own invalidation boundary rather than that machinery.

## Refactors and architecture
- Install every page of a multi-page install-root release, not just the primary.
  `_try_install_root` uses `candidate.final_target` alone and ignores `candidate.pages`, so a tier-1 release ships its primary with no `group` recorded.
  Tier 2 installs the whole bundle as one unit (ADR-0042, ADR-0046); tier 1 does not, and nothing says why.
  A gap rather than a regression -- it predates the grouping work.

### Maintainability

- Decide whether `Config` binds XDG paths per instance or intentionally at import time, then make discovery consistent.
  Current frozen module globals make ordinary environment monkeypatches ineffective after import; this is a configuration-lifecycle decision deserving an ADR.
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

## Settled exclusions

- Do not reconstruct tier-1 direct provider links.
  ADR-0046 refused: "not under `output_dir`" is not evidence of a provider root, and adopting one would let MANIAC replace and later remove a symlink the user owns.
  A fully lost manifest loses tier-1 ownership entirely as the accepted cost; real evidence would be a target resolving beneath a live provider install root, which `sources.candidates` can already establish, should that ever change.

- Do not reattempt a `maniac list` fact cache without reading ADR-0045 first.
  Measured slower than no cache and abandoned; the implementation is archived unmerged at `feature/list-fact-cache` (`604581d`, measurements at `328c93c`).
  Three of its findings are reasons a whole class of fact cannot be cached at all, not incidental details of that attempt.
  ADR-0041 selected DiskCache for a store that no longer exists; ADR-0045 supersedes it.
  `--no-cache` was deferred behind a contract that was never settled and is now moot.

- Do not resolve non-GitHub upstreams (GitLab, Codeberg) for now.
  `npm`, `pipx`, `uv`, `go`, `homebrew` and `cargo` all discard a repository URL that `discovery._clean_git_url` leaves unchanged, so such a project resolves to nothing even when its metadata declares the URL outright.
  Decided 2026-09-22: cut rather than deprioritized -- this machine's manifest has no tool that needs it (both entries are tier=synthesis), so the gap has zero real impact today.
  Revisit if a real installed tool ever resolves to a non-GitHub host; it would be one cross-provider policy change, not six provider bugs, and it pairs with the Source-link exclusions below.
  Never infer a repository from a bare executable name if this is revisited: prior collisions include `fmt` -> `nushell/nufmt`, `od` -> `todotxt/todo.txt-cli`, and GNU `envsubst` -> `a8m/envsubst`.

- Do not admit bounded documentation roots in monorepos, feed an installed manpage into synthesis as reference material, or add a dense-table row guide.
  Decided 2026-09-22 backlog triage: all three are speculative breadth beyond this solo installation's two real, both-synthesis-tier tools -- no current tool exercises a monorepo doc root or benefits from reference-anchored synthesis, and no one has asked for themed row striping.
  Re-add if a real tool or a real complaint makes one of these concrete.

- Do not thread `dry_run` into the discovery layer to stop `install --dry-run` writing to the repository cache.
  Decided 2026-09-16: the cache is disposable repository storage, so a preview populating it is not a mutation worth preventing.
  What dry-run must not touch is the manpath, the manifest, `output_dir` and `intermediate_dir`, and it does not -- it opens no manifest transaction either.
  The original item asked for caches too; that half is withdrawn rather than outstanding.
  The flag's help says "without installing or generating anything", which is true, and `pipeline.py` carries a comment saying the crawl and doc fetch still run, so nothing claims otherwise.
  Note if this is ever revisited: the three tests asserting `not cfg.cache_dir.exists()` pass only because discovery is monkeypatched or the fake provider has no source, so they would not catch a change here either way.

- Do not change what `maniac list` prints when stdout is not a terminal.
  It renders the Tool column alone, because Rich falls back to 80 columns and drops the other three.
  Decided 2026-09-15: bare names are the wanted pipe output, so the accident and the intent coincide.
  A plain-text width, a `--plain` mode and `--json` were all considered and are not wanted now; revisit only if a real consumer needs the other columns.

- Do not use linuxcommandlibrary.com: its generated, lossy, weakly attributed corpus is not a trustworthy documentation source.
- Do not fetch man7.org, Debian/Ubuntu manpage mirrors, or GNU online manuals where local pages and Info already supply the same content.
  Install `manpages-dev` for missing local sections instead.
