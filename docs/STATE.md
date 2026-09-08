# Implementation State

## Configuration and LLM access

ADR-0011 removed the `backend` setting, both model alias tables, and the `agy` subprocess integration.
A model is now a literal LiteLLM identifier; there is no translation layer.
`maniac/defaults.toml` ships in the wheel holding a flat `[providers]` table mapping provider to default model, plus `[limits]` and `[timeouts]` tables for resource and timeout configuration.
User settings live in `$XDG_CONFIG_HOME/maniac/config.toml` with optional keys `provider`, `model`, and `reasoning_effort`; `.env` continues to hold credentials.
A leftover `config.yaml` with no `config.toml` present raises an error naming the format change.
`pyyaml` was dropped as a dependency.
`Config.work_base_dir` and the `work_base_dir` parameter on `run_llm_synthesis` and `evaluation/judge.py`'s four functions are gone too -- only the removed agy backend ever wanted a scratch directory, and nothing had created, written or cleaned `$XDG_CACHE_HOME/maniac/tmp` since.
The field was constructor-only: `_load_config_file` is an allowlist copying just `provider`, `model`, `reasoning_effort` and `[classification].min_words_per_flag`, so `work_base_dir` was never settable from `config.toml` and no existing user config changes meaning.

Model resolution follows one chain: CLI flag, then `config.toml` settings, then environment (`MANIAC_MODEL`), then the first configured provider whose API key is present, then that provider's default model.
Four resolution decisions not settled by ADR-0011: `model` in `config.toml` outranks `provider` there; `reasoning_effort` precedence mirrors the model chain; no `MANIAC_PROVIDER` env var exists; model validation strips the provider prefix before checking the registry.
Provider env-var names come from `litellm.validate_environment`, so no hand-maintained table exists.
`reasoning_effort` is sent only when `litellm.supports_reasoning` reports the model accepts it, and a resolved model is validated against `litellm.models_by_provider` with an error naming the model rather than a raw LiteLLM failure.

## Repo-shipped manpage detection

`manpages.find_repo_manpage(repo_dir, binary_name)` globs a repo's root and its `man`/`doc`/`docs`/`share/man` trees for `<bin>.[1-9]` then `<bin>-*.[1-9]` (mirroring the glob `~/.config/mise/tasks/system-install` uses), each pattern expanded across `_COMPRESSION_SUFFIXES` (`""`, `.gz`, `.bz2`, `.xz`, `.zst`), rejecting help2man-generated hits.
`REPO_MANPAGE_DIRS` holds path-prefix tuples rather than top-level names, so `share/man/man1/` is reached while an unrelated `vendor/man/` still is not.
An exact `<bin>.<section>` match always wins over a `-*` subcommand variant -- naive lexical sorting returns `fzf-tmux.1` before `fzf.1` since `-` sorts before `.`.
`docs.discover_repo_manpage` resolves (cloning if needed) through the same cache path `fetch_and_extract_docs` uses, via `docs.resolve_repo_dir`, then delegates to `find_repo_manpage`.

Confirmed against real repos: finds `fzf.1` at `junegunn/fzf` and `tmux.1` at the root of `tmux/tmux`.
Cannot find a page for `eza-community/eza` or `sharkdp/bat`, whose manpages are release-time-generated assets never committed to the tree; see `docs/BACKLOG.md` for that gap and the separate `tmux`/`tmux-builds` docs-repo mismatch this testing surfaced.

A compressed page such as `pandoc.1.gz` now matches, and the search reaches `share/man/man<N>/`.

`manpages.find_install_root_manpages(root, binary_name)` shares `find_repo_manpage`'s glob patterns but returns every match instead of the first and drops the help2man filter -- ADR-0016 defers quality judgement past tier 1, so a help2man-generated page taken from the install root is still authoritative.
It walks a dedicated `_iter_install_root_manpage_files`, not `find_repo_manpage`'s own tree walker: an install root is one package's extracted content, not an arbitrary checkout that can bury a `man/` dir at any depth in a vendored tree, so it tolerates one release-archive wrapper directory below the root (mise's `gh` install keeps `gh_2.90.0_linux_amd64/share/man/man1/`, not `share/man/man1/` directly under root) before requiring a `REPO_MANPAGE_DIRS` prefix -- a laxness `find_repo_manpage` deliberately does not carry, confirmed by its own `vendor/man/`-rejecting test still passing unmodified.
All eight providers' `local_docs()` (`maniac/sources/providers/*.py`) call it with `(inst.root, inst.binary)` to list pages from the install root; the subcommand pattern keeps a package's sibling binaries in one result (`pandoc-lua.1.gz`, `pandoc-server.1.gz` alongside `pandoc.1.gz`), sharing one doc set across them.
Verified live on the development system: `pandoc` returns its three pages (`man pandoc` itself reports no manual entry), `fzf` 1, `just` 1, `zoxide` 6, `pastel` 23, `gh` 220 -- all six match ADR-0016's predicted counts, `gh` and `pastel` only after the wrapper-directory tolerance above was added (both returned 0 without it, since mise nests their `share/man`/`man` trees one level deeper than a plain package root).
`local_docs()` is wired into the `install` command's tier-1 check for authoritative pages; `status` enumerates installations via the provider registry and reports management state per binary.

`_opener_for` decompresses `.zst` through `zstandard.ZstdDecompressor.stream_reader` (`_zstd_open`, matching `discovery.py`'s use of the library rather than the `zstandard.open` wrapper), so a `.zst` page that matches by filename can now be read and `find_repo_manpage`'s `is_help2man_manpage` gate no longer fails open on one; `read_manpage_source` shares the opener and gains the same.
No such page has been observed on the development system, so this closed a latent gap rather than a live defect.

## Installed-vs-generated manpage comparison

`maniac eval <tool> --against-installed` locates the installed page via `manpages.find_installed_manpage_path` (`man -w <tool>`), reads it in full with `manpages.read_manpage_source` (same decompression as `_read_prefix`, without its 8 KB cap), and passes both it and MANIAC's generated Markdown to `evaluation.judge.compare_manpages`.
The generated page is scored with `evaluate_manpage` (deterministic Markdown-structure checks plus the LLM judge); the installed page, being raw roff rather than Markdown, is scored with `run_llm_judge` alone.
A third call (`run_comparison_judge`, prompt in `maniac/templates/compare_prompt.md`) judges both pages head-to-head against the same reference context and returns a prose verdict, not just two independent scores.
`parse_evaluation_json`'s JSON-extraction/repair logic is shared through `_extract_json_object`, reused by `parse_comparison_json`.
`compare` was a standalone command until ADR-0013 folded it into `eval` behind `--against-installed`, since both took the same generated-manpage-plus-context inputs; `maniac/cli/evaluate.py` dispatches on the flag to `compute_eval` or `compute_compare`, sharing option parsing and manpage/context resolution.

Not yet run against a live LLM -- covered by unit tests only (`tests/test_compare.py`, `tests/test_manpages.py`), all with `run_llm_synthesis` mocked.
A staged `fzf` markdown/context pair is ready at `~/.local/share/maniac/manpages/fzf.1.md` / `~/.local/state/maniac/intermediate/fzf_context.md` for whenever the API is available; see `docs/BACKLOG.md`.

## CLI command surface (ADR-0013)

`maniac/cli.py` (834 lines, nine commands) is now `maniac/cli/`, one module per command group: `options.py` (shared `--output-dir`/`--cache-dir`/`--model`/`--force`/`--dry-run` type aliases), `render.py` (Rich rendering only), `source.py` (`source crawl`/`source docs`), `install.py`, `evaluate.py` (`eval`, with `compare`'s old behaviour behind `--against-installed`), `status.py`, `uninstall.py`.
`list`, `list-missing`, `generate`/`generate-missing` and the standalone `compare` command are gone -- `generate` was renamed `install` by ADR-0016, removed outright rather than kept as an alias.

Every command computes a result dataclass (`EvalOutcome`, `CompareOutcome`, `UninstallOutcome`, `StatusRow`) before a thin render function prints it, so a test can assert on the decision directly instead of scraping `res.output`.
`tests/test_cli.py`, `tests/test_eval.py` and `tests/test_compare.py` do this now; a handful of `CliRunner`-based smoke tests remain per module, checking only short fixed strings that cannot wrap at any terminal width.
The `_plain_console` fixture's `width=400` pin and its `TODO:` are gone with it -- no remaining assertion depends on how Rich wraps a long dynamic value such as a path.

`status [TOOL...]` replaces `list` and `list-missing`.
`--candidates` and its `[classification] min_words_per_flag` threshold are gone by ADR-0016 -- judging an existing page's quality left scope, so `status` reports a fact about installation per binary, never a verdict on quality.
With no arguments it walks `discovery.enumerate_installations()` ($PATH, resolved through the provider registry); with tool names it resolves exactly those via `discovery.find_installation`, unfiltered.
The manpath is never scanned.

Every binary falls into one of ADR-0016's three states (`ActionState`): `SHIPS_UNINSTALLED` (the install root ships a page for it and MANIAC hasn't installed one), `NO_PAGE` (neither), or `MANAGED` (a page already sits at `Config.man_dir` for it).
`_state_for` checks `manpages.find_managed_manpage(cfg.man_dir, tool)` before the install-root page, not after: a tier-1/2 install (ADR-0016) copies its source file verbatim and carries no MANIAC provenance header, so a page already installed there would otherwise keep reporting `SHIPS_UNINSTALLED` forever.
A named tool no provider claims still gets a row (`NO_PAGE`, unless a managed page already covers it) rather than nothing, matching ADR-0013's rule that a named tool always reports something.

The unit is the binary, not the package: `pandoc`, `pandoc-lua` and `pandoc-server` are three separate `Installation`s from one mise install root, each getting its own `StatusRow`.
Package identity (`Installation.package`) groups rows only for the Rich table's display -- `_grouped_for_display` collapses binaries sharing one `(provider, package, state)` into a single row, so `pandoc`'s already-managed row stays separate from its still-uninstalled `pandoc-lua`/`pandoc-server` siblings, which collapse together.
Redirected to anything but a terminal, `status` prints bare binary names -- one per line, deduplicated, via `print()` rather than the Rich console, never collapsed by package -- which is what makes `maniac status | xargs maniac install` work; `--names` forces the same output on a real terminal.

`install` (`generate` renamed by ADR-0016) resolves through three tiers and always installs -- a `--install`/`--no-install` flag inherited from `generate` was dropped once the command's own name made `--no-install` self-contradictory; the tier-3 (synthesis) case's compiled markdown/roff still lands in the XDG output dir regardless, so it stays reviewable before trusting it.
`install` with zero tool names exits 0 quietly rather than raising Typer's missing-argument error, since a `$(maniac status)` expansion can legitimately be empty.

## Verification performed

`just check` (Ruff format, Ruff lint, `ty`, pytest) passes with 347 tests, exit 0.
`ruff format` reaches Python code fences inside markdown; hand-aligned comments in such fences are reformatted to its canonical output.
That behavior predated this work (confirmed against `f518a8a`), meaning `just check` was already running against this constraint when the work landed.

## Provider registry and installation detection (ADR-0015)

ADR-0015 replaces symlink-prefix matching with a provider per installer, each implementing three methods (`detect`, `resolve_source`, `local_docs`) over a shared `Installation` type.
The `Installation` dataclass in `maniac/models.py` holds `provider`, `root`, `binary`, `package`, `version`, `parent` and reachable `sources`, consumed uniformly by every layer above detection.
The `Provider` protocol sits in `maniac/sources/providers/base.py`; `ProviderRegistry` and the module-level singleton `registry` live in `maniac/sources/providers/registry.py`, re-exported from `sources/providers/__init__.py`.
Name-only registry matching is removed everywhere, closing the gap where `discover_repo("envsubst")` returned `a8m/envsubst` and a page describing an unrelated program would have been installed.

Eight providers are registered in this order: `local_lib`, `uv`, `mise`, `npm`, `pipx`, `cargo`, `go`, `homebrew`, each in `maniac/sources/providers/{name}.py`.
When `discovery._resolve_symlink_target` encounters a binary, it loops over `registry`, calling `detect(bin_path)` on each provider until one claims it.
The three path markers (`/.local/lib/`, `/.local/share/uv/tools/`, `/.local/share/mise/installs/`) were confirmed mutually exclusive across 227,002 real paths under `~/.local` on the development system, establishing that registration order does not determine correctness.

`LocalLibProvider` detects checkouts under `~/.local/lib/`; `UvProvider` detects tool venvs under `~/.local/share/uv/tools/`; `MiseProvider` detects installs under `~/.local/share/mise/installs/`.
`local_lib`'s `version` is always `None` since a raw checkout has git history, not a release; `uv`'s is best-effort from a `*.dist-info` directory and may also be `None`.
A `MiseProvider` install's `parent` is initially `None`, built later from `.mise.backend.toml` when that file names a composable backend (npm or pipx).

`CargoProvider` detects membership in `$CARGO_HOME/.crates2.json`'s `installs` (gating on `.crates2.json` records, not `$CARGO_HOME/bin` directory presence, to avoid misattributing rustup's own shims), verified live via `hexyl` (v0.17.0).
`GoProvider` reads `$GOBIN`, then `$GOPATH/bin`, then `~/go/bin`, deriving `version` from `go version -m <bin>`'s build metadata, verified live via `goimports` (v0.49.0).
`NpmProvider` detects `<prefix>/lib/node_modules/<pkg>/` and resolves `package.json`'s `repository` field; `PipxProvider` detects `$PIPX_HOME/venvs/<pkg>/` and resolves `METADATA`'s `Project-URL`/`Home-page`; `HomebrewProvider` detects `<prefix>/bin/foo -> ../Cellar/foo/<version>/...` and resolves via `brew info --json=v2`.
None of npm, pipx, or homebrew is installed on the development system, so their `detect()` methods are untested; their `resolve_source()` methods have proof of work through mise composition (npm and pipx only) and can be trusted, while homebrew's `resolve_source` remains wholly unverified.

`MiseProvider.resolve_source` reads `<install root>/.mise.backend.toml` first, resolving `aqua:` and `github:` backends directly (their package identity is already `owner/repo`), and other backends to `None` rather than guessing.
Where the backend file is absent, resolution falls back to `discovery._resolve_from_mise(inst.package, inst.binary)`, keyed on directory name with old prefix parsing removed, with local mise config (`_check_mise_toml`) checked first.
Of 59 mise-managed binaries under `~/.local/bin`, 20 carry `.mise.backend.toml` with resolvable backends; 18 resolve through the fallback (mostly via local config aliasing); the remaining 21 are `python`, `npm`/`pipx` installs, and legacy directories without backend records -- all correctly unresolved, lacking providers for those backends yet.

`MiseProvider` composes npm and pipx backends by building `Installation.parent` from the on-disk shape (`_COMPOSED_BACKENDS` knows both), delegating their source resolution to the npm and pipx providers found in `registry`.
Evidence: `yaml-language-server` (npm) and `tlpui` (pipx) newly resolve through composition on the development system; pre-backend-file installs like `npm-bash-language-server` correctly stay unresolved.

## Installation workflow and tiering (ADR-0016)

ADR-0016 establishes three-tier install precedence for finding authoritative pages: tier 1 (install root), tier 2 (repository at matched version), tier 3 (LLM synthesis), replacing prior name-based matching.
The `install` command (renamed from `generate`) uses `maniac/orchestration/install.py`'s `run_install`, selecting tiers via `--generate` (tier 3 only) or `--no-generate` (tiers 1-2 only).

Tier 1 calls each provider's `local_docs(inst)` to list pages available from the install root.
`manpages.select_primary_manpage` filters to the one page naming the binary itself (picking `pandoc.1.gz` over its `pandoc-lua`/`pandoc-server` siblings, using the subcommand pattern to share one doc set across related binaries).
Evidence from the development system: `pandoc` (3 pages), `fzf` (1), `just` (1), `zoxide` (6), `pastel` (23), `gh` (220) all ship pages in install roots.

Tier 2 refuses if `inst.version is None` (a raw checkout cannot be version-matched); otherwise clones the git tag naming the installed version (`v<version>` or bare `<version>` via `git ls-remote --tags`), returning `None` if no tag matches.
A page found is validated by `manpages.manpage_documents`, checking that its `.TH`/`.Dt` title matches the binary name (not proving the repository is correct upstream, only that title and filename agree).

Tier 3 runs the full LLM synthesis pipeline via `run_pipeline`, imported lazily so `--no-generate` never loads it.

Verified live: `maniac install pandoc --no-generate` finds and installs `pandoc.1.gz` from the install root with no LLM access needed.
`maniac status` on the development system (65 binaries across all eight providers) reports: 2 `SHIPS_UNINSTALLED`, 21 `MANAGED`, 42 `NO_PAGE`.
`maniac status pandoc-lua pandoc-server | xargs maniac install --no-generate` installs both siblings from the install root with no LLM call.

## Installed-pages manifest (ADR-0017)

ADR-0017 made a manifest the authoritative record of which pages MANIAC installed, replacing the provenance header as the thing `install`, `uninstall` and `status` branch on.
The header stays on synthesized pages as informational metadata; no command reads it to decide ownership.

`maniac/manifest.py` holds the store at `Config.manifest_path` (`$XDG_STATE_HOME/maniac/installed.json`), schema version 1.
An `Entry` is `path`, `tier` (`Tier.INSTALL_ROOT`/`REPOSITORY`/`SYNTHESIS`), `source`, `checksum` (sha256 hex) and an optional `backup`.
The surface is `load`, `record`, `lookup`, `forget` and `checksum_of`, each taking an optional `Config`.
Loading tolerates corruption: a missing file, unreadable file, bad JSON or wrong `version` yields an empty manifest, and a single malformed entry is skipped rather than voiding the file.
Writes are atomic -- a `.tmp` sibling then `Path.replace` -- so a crash mid-write cannot leave a torn file.

The entry is written before the copy, not after, settling what ADR-0017 left open.
A crash between the two leaves an entry naming a file that does not exist, which `status` can see, rather than an untracked page on disk that nothing could attribute; a copy that raises restores the prior entry, or forgets a freshly written one.

Ownership by record rather than by inference closed two defects.
`uninstall_manpage` reconstructed its target as `<tool>.1` with no compression suffix, so a tier-1 `pandoc.1.gz` was unreachable rather than merely misreported; it now removes the recorded path.
`status`'s `MANAGED` came from `find_managed_manpage`, a location-and-filename test over the shared `$XDG_DATA_HOME/man/man1`, so a page MANIAC never installed reported as managed; that helper is deleted and `_state_for` consults the manifest.

A recorded page whose bytes no longer match its checksum is treated as foreign: left in place and reported through the existing `UninstallResult.foreign_kept`, with no backup restored and no entry forgotten, unless `uninstall --force` overrides.
A recorded page whose file has vanished is not a mismatch but the crash case above, and is forgotten cleanly.

Vendor backups moved out of the manpath.
`install --force` writes the displaced page to `Config.backup_dir` (`$XDG_STATE_HOME/maniac/backups`) and records its path on the entry, where it previously dropped a `.maniac_bak` sibling into the directory `man` scans.
The relocation also fixed a live bug: the backup was created as `<page>.maniac_bak` but looked for as `<tool>.1.maniac_bak`, so a compressed vendor page was backed up and then never restorable.
Because backups and pages now sit under different XDG roots, and so possibly different mounts, restore and migration use `shutil.move` rather than `Path.rename`, which raises `EXDEV` across filesystems.

Migration seeds the manifest on a load that finds no file, scanning `man_dir` alone for header-carrying pages and recording each as tier `synthesis` with its current checksum.
`output_dir` is deliberately not scanned: it holds staging copies, and seeding from it let a leftover artifact report as `MANAGED`.
A stray `.maniac_bak` in `man_dir` is relocated to `backup_dir` and attached to its entry; one matching no entry is left alone rather than deleted.
Pages installed at tiers 1 and 2 before this change carry no trace and cannot be seeded -- they were already reported foreign under the old code, so nothing that worked was lost.

Verified live against a real installation, with every XDG root pointed at a throwaway directory.
`maniac install pandoc --no-generate` took tier 1 from mise's `pandoc/3.10.2` install root and wrote `pandoc.1.gz` still compressed and byte-identical to source (sha256 `af7789d7...9e4d485`, matching the manifest's recorded checksum); `status` reported `MANIAC-managed`; `uninstall` removed the page and emptied the manifest.
That last step is the round trip that was impossible before: the compressed tier-1 page `uninstall` could not name.
A hand-placed `faketool.1` in the same manpath reported `no page anywhere` rather than `MANAGED`, confirming the `status` over-claim is gone.

## Performance and capability

Evidence from the development system: resolving 588 PATH symlinks through the provider registry takes 1.82s, compared to 1.96s to classify 5504 manpages by their structure (plus 0.84s of fixed interpreter startup), showing that the provider model buys capability, not speed, and the speed argument should not be made again.

One known limitation: a tier-1/2 install copies the page as-is with no MANIAC provenance header (unlike synthesis), so `maniac uninstall <tool>` afterward cannot distinguish it from a pre-existing vendor page and reports it "foreign, kept in place" rather than removing it -- `install` and `uninstall` are not yet a full round trip for tiers 1-2.
