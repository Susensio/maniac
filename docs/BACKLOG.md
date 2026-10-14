# Backlog

Open work nobody has committed to.
Each entry names something someone could start, or what would unblock it.
Settled decisions live in `docs/adr/`; defects with a line to sit beside are marked there, and `rg -n 'BUG:|TODO:'` lists them.

## Bugs and correctness

- Show option overlap in `why` as further evidence about the documentation repository (ADR-0067).
  Measured 2026-10-14: `RobertCraigie/pyright-python` mentions 1 of the 18 long options `pyright --help` prints; psf/black, python/mypy, pycqa/flake8 and python-poetry/poetry mention 84-100% of theirs.
  Detection does not use it: it needs the recorded repository's whole tree, a tool with only short options (tmux) gives none, and clap's derive style spells `--dry-run` as `dry_run` in source.

## Refactors and architecture

- Re-measure warm `maniac scan` on a real inventory, and cut what is left if it is still slow there.
  Profile on record (2026-10-13, at 19cc4c6, a cloud container: 1922 binaries, 20 shown, 4 cores): ~2.7s end to end, after 19cc4c6 stopped a `**` glob over each uv/pipx venv (it was ~5.3s).
  The 18 `api.github.com` requests in that profile (~1.8s of wall-union) were the container's proxy refusing GitHub with 403, which is never cached; where GitHub answers, release metadata is cached a day and a 404 an hour, and the per-URL cache lock makes a package's sibling binaries share one request.
  A refusal or rate limit is now remembered for the rest of the run, never on disk, so siblings no longer ask again.
  Without those, what remains is `git -C` ~0.4s, local classify ~0.4s, `locate_all` and `_build_inventory` ~0.3s each, startup ~0.5s.
  Measure thread-aware (wall-clock union across the pool), never by summing per-call durations or plain cProfile -- both misled before.
  Also compare `LOCAL_CLASSIFY_WORKERS=1` against the default: a pool slower than one worker means syscall-heavy work queued on the GIL, which is how the glob was found.

## Features and discovery

- Seed generation from two more pages of the same software (`candidates.seed_for` takes the first it finds).
  A losing `$PATH` copy's shipped page (`Installation.losers`), once proven the same software by package or repository (ADR-0055); `locate` does not collect losers yet, only `locate_all` does.
  The page `install --force` replaced (its backup), if something beyond the shared name ties it to the tool.
- Extract bounded documentation from installed package roots (system packages are out of scope, ADR-0059): recognized local docs, Info pages, package metadata as supplementary context, and absolute-path help.
  Preserve each source's provenance.
- Add tldr-pages as an examples source, preferring an installed tealdeer cache before its release zip and recording provenance in generated output.
  Its examples are additive; do not substitute it for authoritative option documentation.

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
- Retain competing providers claiming the same `bin_path` at one `$PATH` entry, so `why` can say two installers claim it.
  `_detect_via_registry` stops at the first matching provider, and it is also the single-name lookup path behind `resolution.locate`, so collecting all claims changes that hot path's cost.
  Parked by the user's choice (2026-10-14); unblocked by a real case of two installers claiming one binary.
- Completions for shells beyond bash, zsh and fish (nu, PowerShell, elvish).
  Parked by the user's choice (2026-10-14); unblocked by someone using one.
- Exercise the standalone Homebrew `detect()` against a real installation.
  Mise-composed metadata coverage does not verify its path layout; npm and pipx are covered by `tests/integration/test_providers.py`.
  Blocked on a real Homebrew installation.
