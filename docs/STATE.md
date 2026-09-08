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

## Manpage classification facts

ADR-0012 replaced binary Help2man classification with a facts-only layer stored in `maniac/classification.py`.
Per-page stored facts are `word_count`, `tp_count`, `sections`, `has_examples_section`, `dialect`, plus path, existence, whether MANIAC authored it, and reachable sources for the tool.
There is deliberately no verdict, state vocabulary, or combined score — the verdict layer is deferred to real output per ADR-0012.
Generator origin covers help2man, Pod::Man, Pandoc, txt2man, DocBook XSL, po4a and cobra, identified by marker in the first 8 KiB of the roff source; generator origin is internal and not user-facing.

`dialect` is `man`, `mdoc`, or `unknown`, detected by comparing counts of dialect-distinctive macros (`.Dd`/`.Dt`/`.Sh`/`.Bl`/`.It`/`.Nm`/`.Fl`/`.Op`/`.Ar`/`.Cm` for mdoc, `.TH`/`.SH`/`.TP`/`.IP`/`.PP`/`.B`/`.BR`/`.BI` for man(7)) at line starts in the same page prefix already read; whichever set dominates wins, a tie or neither present is `unknown`.
`tp_count` and `sections` are computed against the detected dialect: `.TP`/`.SH` for man(7), `.It`/`.Sh` for mdoc, so both facts mean the same thing across dialects. `word_count` was already dialect-agnostic (it counts every non-comment line regardless of macro) and needed no change.

The cache lives under `$XDG_CACHE_HOME/maniac/` keyed on `(path, mtime, size)`, carries `CACHE_SCHEMA_VERSION`, and is discarded wholesale on a version mismatch.
A malformed row or corrupt cache file is treated as a miss rather than raising; a cache is an optimisation and corruption in one row should cost recomputing that row, not the entire cache.
A dump entry point exists at `python -m maniac.classification`.

Two decisions ADR-0012 did not settle: `.SS` subsections are not counted in `sections`, only top-level `.SH`; `has_examples_section` matches `EXAMPLES` or `USAGE` case-insensitively against exact section names, not substrings.
One decision this work left unsettled: a page whose macro counts tie, or that carries neither macro set, is stored as `dialect=unknown` with `tp_count`/`sections` computed as man(7) (the default), since a dialect-blind fallback has to pick one; the verdict layer should treat `unknown` as no evidence rather than trusting those counts.

Evidence from a real run on the development system: 5504 pages classified; generator origins split as none 3741, Pod::Man 867, DocBook XSL 654, help2man 220, pandoc 17, txt2man 4, po4a 1; 15 pages resolved a reachable source under the ADR-0008 installation-tied rule; 2 pages are MANIAC-authored; dialects split as man 5411, mdoc 80, unknown 13.
`tmux.1` is now correctly detected as mdoc, reporting `tp_count=1017` and 27 sections instead of the prior `tp_count=0`/`sections=[]`; `fzf.1` (man(7), `tp_count=128`) is unchanged.

## Repo-shipped manpage detection

`manpages.find_repo_manpage(repo_dir, binary_name)` globs a repo's root and its `man`/`doc`/`docs`/`share/man` trees for `<bin>.[1-9]` then `<bin>-*.[1-9]` (mirroring the glob `~/.config/mise/tasks/system-install` uses), each pattern expanded across `_COMPRESSION_SUFFIXES` (`""`, `.gz`, `.bz2`, `.xz`, `.zst`), rejecting help2man-generated hits.
`REPO_MANPAGE_DIRS` holds path-prefix tuples rather than top-level names, so `share/man/man1/` is reached while an unrelated `vendor/man/` still is not.
An exact `<bin>.<section>` match always wins over a `-*` subcommand variant -- naive lexical sorting returns `fzf-tmux.1` before `fzf.1` since `-` sorts before `.`.
`docs.discover_repo_manpage` resolves (cloning if needed) through the same cache path `fetch_and_extract_docs` uses, via `docs.resolve_repo_dir`, then delegates to `find_repo_manpage`.

Confirmed against real repos: finds `fzf.1` at `junegunn/fzf` and `tmux.1` at the root of `tmux/tmux`.
Cannot find a page for `eza-community/eza` or `sharkdp/bat`, whose manpages are release-time-generated assets never committed to the tree; see `docs/BACKLOG.md` for that gap and the separate `tmux`/`tmux-builds` docs-repo mismatch this testing surfaced.

The two latent defects Stage 5 named as prerequisites are fixed: a compressed page such as `pandoc.1.gz` now matches, and the search reaches `share/man/man<N>/`.

Stage 5 landed: `manpages.find_install_root_manpages(root, binary_name)` shares `find_repo_manpage`'s glob patterns but returns every match instead of the first and drops the help2man filter -- ADR-0016 defers quality judgement past tier 1, so a help2man-generated page taken from the install root is still authoritative.
It walks a dedicated `_iter_install_root_manpage_files`, not `find_repo_manpage`'s own tree walker: an install root is one package's extracted content, not an arbitrary checkout that can bury a `man/` dir at any depth in a vendored tree, so it tolerates one release-archive wrapper directory below the root (mise's `gh` install keeps `gh_2.90.0_linux_amd64/share/man/man1/`, not `share/man/man1/` directly under root) before requiring a `REPO_MANPAGE_DIRS` prefix -- a laxness `find_repo_manpage` deliberately does not carry, confirmed by its own `vendor/man/`-rejecting test still passing unmodified.
All eight providers' `local_docs()` (`maniac/sources/providers/*.py`) call it with `(inst.root, inst.binary)` in place of the Stage 2 `[]` stub; the subcommand pattern keeps a package's sibling binaries in one result (`pandoc-lua.1.gz`, `pandoc-server.1.gz` alongside `pandoc.1.gz`), which Stage 7 needs to share one doc set across them.
Verified live via `MiseProvider.detect` against every install the roadmap names on the development system: `pandoc` returns its three pages (`man pandoc` itself reports no manual entry), `fzf` 1, `just` 1, `zoxide` 6, `pastel` 23, `gh` 220 -- all six match ADR-0016's predicted counts, `gh` and `pastel` only after the wrapper-directory tolerance above was added (both returned 0 without it, since mise nests their `share/man`/`man` trees one level deeper than a plain package root).
`local_docs()` is still not wired into any user-facing command -- `status` and `run_pipeline` still treat every candidate as needing an LLM-generated page; that wiring, and the `generate`-to-`install` rename ADR-0016 also calls for, is Stage 6's job.

One gap remains open, recorded in `docs/BACKLOG.md`: `_opener_for` has no zstd branch, so a `.zst` page now matches by filename but cannot be read, and `find_repo_manpage`'s `is_help2man_manpage` gate fails open on it (moot for `find_install_root_manpages`, which never calls that gate).
No such page has been observed on the development system.

## Installed-vs-generated manpage comparison

`maniac eval <tool> --against-installed` locates the installed page via `manpages.find_installed_manpage_path` (`man -w <tool>`), reads it in full with `manpages.read_manpage_source` (same decompression as `_read_prefix`, without its 8 KB cap), and passes both it and MANIAC's generated Markdown to `evaluation.judge.compare_manpages`.
The generated page is scored with `evaluate_manpage` (deterministic Markdown-structure checks plus the LLM judge); the installed page, being raw roff rather than Markdown, is scored with `run_llm_judge` alone.
A third call (`run_comparison_judge`, prompt in `maniac/templates/compare_prompt.md`) judges both pages head-to-head against the same reference context and returns a prose verdict, not just two independent scores.
`parse_evaluation_json`'s JSON-extraction/repair logic is shared through `_extract_json_object`, reused by `parse_comparison_json`.
`compare` was a standalone command until ADR-0013 folded it into `eval` behind `--against-installed`, since both took the same generated-manpage-plus-context inputs; `maniac/cli/evaluate.py` dispatches on the flag to `compute_eval` or `compute_compare`, sharing option parsing and manpage/context resolution.

Not yet run against a live LLM -- covered by unit tests only (`tests/test_compare.py`, `tests/test_manpages.py`), all with `run_llm_synthesis` mocked.
A staged `fzf` markdown/context pair is ready at `~/.local/share/maniac/manpages/fzf.1.md` / `~/.local/state/maniac/intermediate/fzf_context.md` for whenever the API is available; see `docs/BACKLOG.md`.

## CLI command surface (ADR-0013)

`maniac/cli.py` (834 lines, nine commands) is now `maniac/cli/`, one module per command group: `options.py` (shared `--output-dir`/`--cache-dir`/`--model`/`--force`/`--dry-run` type aliases), `render.py` (Rich rendering only), `source.py` (`source crawl`/`source docs`), `generate.py`, `evaluate.py` (`eval`, with `compare`'s old behaviour behind `--against-installed`), `status.py`, `uninstall.py`.
`list`, `list-missing`, `generate-missing` and the standalone `compare` command are gone.

Every command computes a result dataclass (`EvalOutcome`, `CompareOutcome`, `UninstallOutcome`, `StatusRow`) before a thin render function prints it, so a test can assert on the decision directly instead of scraping `res.output`.
`tests/test_cli.py`, `tests/test_eval.py` and `tests/test_compare.py` do this now; a handful of `CliRunner`-based smoke tests remain per module, checking only short fixed strings that cannot wrap at any terminal width.
The `_plain_console` fixture's `width=400` pin and its `TODO:` are gone with it -- no remaining assertion depends on how Rich wraps a long dynamic value such as a path.

`status [TOOL...] [--candidates]` replaces `list` and `list-missing`.
The threshold `--candidates` selects on is `[classification] min_words_per_flag` in `maniac/defaults.toml`, overridable in the user's `config.toml`; ADR-0014 records why it is configuration rather than a command-line option.
With no arguments it reads `classification.collect_facts()` (never recomputing classification) and keeps pages with a resolvable source or that MANIAC already manages; with tool names it reports exactly those, unfiltered.

A named tool the manpath scan never saw now reports an absence row rather than nothing -- `maniac status uv` was silent, which is the common case MANIAC exists to serve.
`classification.absent_facts(tool)` builds it: source resolved through the same `discover_candidate_source` `_classify_page` uses, so the row still shows what MANIAC would generate from, and every measured fact left at its zero or empty value.
`ManpageFacts.path` stays a non-optional `Path`, filled with an `ABSENT_PATH = Path("<absent>")` sentinel; widening it to `Path | None` would have forced a `None` branch through `_facts_to_row`/`_row_to_facts` for a value that can never reach the cache, and `Path()` was rejected because it stringifies to `.` and would read as the cwd in `python -m maniac.classification dump`.
Absence rows are built outside `collect_facts` and never cached -- the cache is keyed on `(path, mtime, size)` and an absent page has none of the three -- so `CACHE_SCHEMA_VERSION` is unchanged.
Three decisions this closed: only named arguments produce absence rows, since enumerating uninstalled tools would mean reinstating the bin-dir scan ADR-0013 removed; a named tool yields an absence row without checking whether its binary exists, for the same reason; and the table renders `-` for Section, Words and Flag entries on an absent page (`_observed`), never `0`, so an unmeasured absence cannot be misread as a measured zero.
`--candidates` needed no change: `candidates.select_candidate` already returns `SELECTED` for `not facts.exists`.
`--candidates` filters further to `candidates.select_candidate(...) is SELECTED` against `Config.min_words_per_flag`, never a CLI-exposed threshold, per ADR-0014.
Columns are observations only (word count, flag-entry count, ownership, source), never a verdict.
Redirected to anything but a terminal, `status` prints bare tool names -- one per line, deduplicated across sections, via `print()` rather than the Rich console -- which is what makes `maniac status --candidates | xargs maniac generate` and `maniac generate $(maniac status --candidates)` work; `--names` forces the same output on a real terminal.

`generate` now installs by default, with `--no-install` to stop after compiling -- it and the removed `generate-missing` disagreeing on this default for the same pipeline was the bug named in ADR-0013.
`generate` with zero tool names exits 0 quietly rather than raising Typer's missing-argument error, since a `$(maniac status --candidates)` expansion can legitimately be empty.

Verified for real on the development system: `maniac status --candidates` selects `gum`, `gh`, `pastel`, `just` and excludes `usage`, `aichat`, `tmux`, `bat`, `fish-lsp`, matching ADR-0014's numbers; `maniac status --candidates | cat` prints the four names bare, one per line.
`maniac status uv hx bat` renders `uv` and `hx` with `-` in Section/Words/Flag entries and live sources (`astral-sh/uv`, `helix-editor/helix`) while `bat` keeps its measured `1919`/`0`; `maniac status uv hx --candidates | cat` prints `uv` and `hx`, so the pipe into `xargs maniac generate` reaches tools with no page at all.

## Verification performed

`just check` (Ruff format, Ruff lint, `ty`, pytest) passes with 250 tests, exit 0.
`ruff format` reaches Python code fences inside markdown, so `docs/ROADMAP.md`'s Stage 1 fence had to be reformatted to its output; the hand-aligned field comments in the roadmap are gone as a result.
That failure predated this work -- confirmed against `f518a8a` -- meaning `just check` was already red on `master` and no stage had actually landed green under it.

## Direction set 2026-09-07 (ADR-0015, ADR-0016)

Two decisions were taken; `docs/ROADMAP.md` sequences the work in seven stages, of which Stages 1-3 have landed.

ADR-0015 replaces symlink-prefix matching with a provider per installer, each answering detection, source resolution and local documentation as three separate methods, and introduces `Installation` as the type every other layer consumes.
Name-only registry matching is removed everywhere rather than left available to explicit requests, closing the gap where `discover_repo("envsubst")` returned `a8m/envsubst` and `generate` would have installed a page describing an unrelated program.

Stage 1 landed: the frozen, slotted `Installation` dataclass sits in `maniac/models.py` next to the existing DTOs; the `Provider` protocol is `maniac/sources/providers/base.py`; `ProviderRegistry` plus the module-level singleton `registry` that owns the ordered provider list is `maniac/sources/providers/registry.py`, both re-exported from `sources/providers/__init__.py`.
Unit tests over `Installation` alone live in `tests/test_installation.py`.

Stage 2 ported mise, uv tools and `~/.local/lib` behind the protocol: `maniac/sources/providers/{mise,uv,local_lib}.py`.
`providers/__init__.py` registers all three into the module singleton on import, in the order `discovery._resolve_symlink_target`'s prior if-elif chain checked them -- `local_lib`, `uv`, `mise` -- so the diff is a pure refactor; the three path markers (`/.local/lib/`, `/.local/share/uv/tools/`, `/.local/share/mise/installs/`) were confirmed mutually exclusive against 227,002 real paths under `~/.local` on the development system before relying on registration order not mattering for correctness.
`discovery._resolve_symlink_target` now loops over `registry`, calling `detect(bin_path)` on each until one claims the path, imported lazily inside the function body to avoid a cycle (`discovery` -> `providers` -> a provider module that imports `discovery` back for reuse).
Detection fills every `Installation` field from the path each provider recognises, with three left empty by nature rather than oversight: `parent` is `None` for all three this stage, since composing mise's backend via `.mise.backend.toml` is Stage 3's job; `local_lib`'s `version` is always `None`, since a raw checkout has git history rather than a release version; `uv`'s `version` is best-effort from a matching `*.dist-info` directory under the tool's venv and is `None` when nothing matches (a non-Python entrypoint, or an odd layout).
`local_docs()` returns `[]` for all three -- wiring it to the install root is Stage 5's job, and returning nothing is not yet an observable behaviour change since nothing calls it.
`resolve_source` reuses `discovery._resolve_from_mise` (mise) and `discovery._clean_git_url` (local_lib) rather than duplicating that logic; the mise-registry download/parse/cache machinery and `_check_mise_toml` still live in `discovery.py`.

`tests/test_provider_registry.py::test_the_module_registry_ships_empty` is replaced by `test_the_module_registry_holds_the_registered_providers`, asserting `[p.name for p in registry] == ["local_lib", "uv", "mise"]`.
The old test asserted the *absence* of Stage 2's deliverable -- that no concrete provider had registered into the singleton -- not any discovery outcome, so it necessarily expired the moment Stage 2 registered three of them into that same singleton; the roadmap's "every existing test passes unmodified" rule is a proof that porting mise/uv/local_lib behind the protocol changes no observable discovery outcome, and registry emptiness was never a discovery outcome to preserve.
Every test that does assert one -- `tests/test_discovery.py`'s `discover_repo`/`discover_candidate_source` suite, unchanged -- still passes unmodified.
`tests/test_providers_{mise,uv,local_lib}.py` are new, covering each provider's `detect`/`resolve_source`/`local_docs` against constructed install layouts under `tmp_path`, including the negative case per provider (a path it must not claim) and the deliberately-empty fields above.

Stage 3 landed: `MiseProvider.resolve_source` reads `<install root>/.mise.backend.toml` first, in `maniac/sources/providers/mise.py`'s new `_read_backend_record`/`_repo_from_backend`.
`full = "aqua:biomejs/biome"` or `full = "github:owner/repo"` gives a GitHub repository directly, since both backends' package identity already is one; every other backend (`npm`, `pipx`, mise's own `core`) resolves to `None` rather than a guess, since turning that package identity into a repository needs its own provider, which is Stage 4's job.
Where the file is absent, resolution falls back to `discovery._resolve_from_mise(inst.package, inst.binary)`, keyed on the install directory name (installation-derived evidence under ADR-0008) with the `github-`/`pipx-`/`npm-`/`cargo-https-github-com-` prefix parsing deleted from it; the local mise config alias/tools check (`_check_mise_toml`) still runs first inside that fallback, since it is the user's own explicit declaration rather than a guess from a name.
Evidence from the development system: of 59 mise-managed binaries under `~/.local/bin`, 20 install directories carry `.mise.backend.toml` with an aqua or github backend and resolve directly from it; 18 more resolve through the fallback, almost entirely via local mise config aliasing a `cargo:`/`github:` install to its real upstream (`cargo-https-github-com-nushell-nufmt`, the roadmap's cited example, resolves this way on this system rather than staying unresolved); the remaining 21 are `python` (mise's `core` backend), `npm`/`pipx` installs, and two legacy directories with neither a backend file nor a matching config entry -- all correctly unresolved, since Stage 4's providers for those backends do not exist yet.

Folded into this stage by ruling: `allow_binary_registry_match` is deleted outright rather than given a home on the `Provider` protocol, closing the gap Stage 2 flagged.
`discovery._resolve_symlink_target`'s `inst.provider == "mise"` special case is gone -- every provider's `resolve_source` is called uniformly now -- and `discover_repo` no longer falls back to `_resolve_from_mise(binary_name, binary_name)` when no provider claims the binary; a tool with no detected installation is reported unresolved rather than guessed from its bare name.
This is a deliberate behaviour change, not merely a refactor: `discover_repo("envsubst")` stops returning `a8m/envsubst`, since GNU `envsubst` is a real file with no mise/uv/local_lib installation behind it and the registry is no longer consulted without one.
The blast radius is every caller of `discover_repo` and `discover_candidate_source` for a binary with no provider-detected installation -- `maniac source docs`, the `generate` pipeline (`maniac/orchestration/pipeline.py`), and `status`'s candidate classification (`maniac/classification.py`) all stop resolving (and therefore stop generating pages for) a name-only registry hit; nothing changes for a binary a provider does detect.
`tests/test_discovery.py::test_discover_repo_uses_official_registry` and `::test_discover_candidate_source_does_not_use_an_executable_name_registry_match` (asserting the deleted permissive paths) are replaced by `::test_discover_repo_does_not_use_the_registry_without_an_installation` and `::test_discover_candidate_source_delegates_to_the_provider_registry`; `::test_resolve_from_mise_prefixed` is deleted outright, since it tested the deleted prefix parsing.

Stage 4 landed: `maniac/sources/providers/{npm,pipx,cargo,go,homebrew}.py`, registered into `registry` after `local_lib`, `uv`, `mise` in the roadmap table's order (`npm`, `pipx`, `cargo`, `go`, then `homebrew` last, the one with the least evidence behind it) -- `test_provider_registry.py` now asserts all eight names, and ADR-0015 breaks a real tie by `$PATH` order rather than registry order regardless.
`discovery.discover_repo`/`discover_candidate_source` no longer gate on `bin_path.is_symlink()` before calling into the registry: an `exists()` check replaces it in `discover_repo`, and `discover_candidate_source` drops the check outright since `shutil.which` already guarantees existence, unblocking detection of the real files cargo and go install.

`CargoProvider` gates on membership in `$CARGO_HOME/.crates2.json`'s `installs`, not on living in `$CARGO_HOME/bin` or being a real file -- the trap ADR-0015 names is that a naive "real file in the bin dir" check would misattribute rustup's own shims (`cargo`, `cargo-clippy`, `cargo-fmt`, `cargo-miri`, `clippy-driver`, `rls`, `rust-analyzer`, `rustc`, `rustdoc`, `rustfmt`, `rust-gdb`, `rust-gdbgui`, `rust-lldb`), all symlinks to one real `rustup` binary sharing that directory on this system.
Verified live against the real installation ADR-0015 names: `hexyl` resolves to `Installation(package="hexyl", version="0.17.0", ...)` from the `.crates2.json` entry `"hexyl 0.17.0 (registry+https://github.com/rust-lang/crates.io-index)"`; all twelve rustup shims correctly return `None`, both from `CargoProvider.detect` directly and through `discovery.discover_candidate_source("rustc")`.
`resolve_source` always returns `None` -- `.crates2.json` records no upstream field, and querying crates.io for one is left undone rather than guessed, matching `UvProvider`'s stance for a plain published package.
Reads `$CARGO_HOME` from the environment only, never `~/.cargo`, per ADR-0015.

`GoProvider` reads `$GOBIN`, then `$GOPATH/bin`, then `~/go/bin`; identity and version come from `go version -m <bin>`, a local subprocess reading the binary's own embedded build metadata rather than a network call.
Verified live: `~/go/bin/goimports` (`GOBIN` unset, so the `~/go/bin` default is the live branch, as ADR-0015 names) detects as `package="golang.org/x/tools/cmd/goimports"`, `version="v0.49.0"`; `~/.local/bin/go`, the mise-managed Go toolchain itself, is correctly never claimed, since it resolves outside `~/go/bin`.
`resolve_source` derives `owner/repo` only from a `github.com/`-prefixed module path; `goimports`'s own module, `golang.org/x/tools`, is a vanity import path with no fixed GitHub mapping without a DNS/HTTP lookup this provider does not make, so `goimports` itself stays correctly unresolved -- not a defect.

`NpmProvider` detects `<prefix>/lib/node_modules/<pkg>/` and resolves `package.json`'s own `repository` field (a string, a `"github:"` shorthand, or `{"type": "git", "url": ...}`), never inferring from the package name.
`PipxProvider` detects `$PIPX_HOME/venvs/<pkg>/` (an explicit env var, then pipx's own XDG default, then its pre-1.0 fallback location) and resolves a `Project-URL`/`Home-page` naming GitHub from the matching dist-info `METADATA`, the same shape `UvProvider` already reads for its own version.
`HomebrewProvider` detects `<prefix>/bin/foo -> ../Cellar/foo/<version>/...`, the symlink shape mise itself uses, and resolves through `brew info --json=v2 <pkg>`'s `homepage` field.
None of the three is installed on the development system -- `npm` itself is mise-managed here but broken (`Cannot find module '.../npm-cli.js'`), pipx and Homebrew are absent entirely -- so all three `detect` methods are written against documented layouts and untested against a real install, the gap `docs/BACKLOG.md` already names for pipx and Homebrew; npm belongs in the same entry, since its own standalone `detect()` is equally unexercised.
`NpmProvider.resolve_source` and `PipxProvider.resolve_source` are not equally unverified, though: mise composition (below) ran both against real installs and got a real answer back, so the `package.json`/`METADATA`-reading logic itself, as distinct from the classic-global/pipx-venv `detect()` path around it, has been proven rather than assumed.

`MiseProvider.detect` now builds `Installation.parent` from `.mise.backend.toml` for a backend `_COMPOSED_BACKENDS` knows the on-disk shape of, replacing the flat `_repo_from_backend` early return for anything past aqua/github; `resolve_source` delegates to the provider of that name found in the shared `registry` (`_find_provider`) instead of returning `None`.
Only `npm` and `pipx` are in `_COMPOSED_BACKENDS`, confirmed against real mise installs on the development system rather than assumed: an npm-backend install's own `package.json` sits under `<install root>/node_modules/<pkg>/` (the install root itself holds mise's own wrapper package, not the tool's), and a pipx-backend install nests its venv one level down at `<install root>/<pkg>/`.
Aqua and GitHub backends are unaffected -- their package identity already is `owner/repo`, so they need no delegate provider and keep resolving through `_repo_from_backend` directly.
Evidence from the development system: two of the three npm/pipx-backend installs newly resolve -- `yaml-language-server` (`full = "npm:yaml-language-server"`) to `redhat-developer/yaml-language-server`, and `tlpui` (`full = "pipx:tlp-ui"`) to `d4nj1/TLPUI` -- both confirmed through `discover_candidate_source`, not only the unit tests built from the same real directory shapes.
Mise-managed npm/pipx installs that predate a `.mise.backend.toml` (`npm-fish-lsp`, `npm-bash-language-server`, `pipx-howdoi`) carry no backend record to compose from and stay unresolved, unchanged from Stage 3; `docs/BACKLOG.md`'s existing item about `bash-language-server`'s `package.json` (reachable only through the local mise-config alias path `_check_mise_toml` already reads, not this stage's `.mise.backend.toml` path) remains open.
No installed binary on this system carries a `cargo:`/`go:` backend record, so composing those is unimplemented rather than guessed at -- `cargo-https-github-com-nushell-nufmt` and its kin predate `.mise.backend.toml` entirely and still resolve through the unrelated directory-name registry fallback Stage 3 already covers.

Stage 5 landed: `local_docs()` is wired to the install root for all eight providers; see "Repo-shipped manpage detection" above for `find_install_root_manpages`, the wrapper-directory tolerance it needed for `gh`/`pastel`, and the live counts confirmed against every install the roadmap names.

ADR-0016 puts authoritative pages ahead of synthesis in a fixed order -- install root, then repository or online with the version matched, then the LLM -- and renames `generate` to `install`, making it the inverse of the previously orphaned `uninstall`, with `--generate` and `--no-generate` selecting the tier.
It supersedes ADR-0014: judging whether an existing page is poor enough to replace leaves the current scope, taking `--candidates` and `min_words_per_flag` with it.

Evidence behind ADR-0016, from the development system: `pandoc` ships `pandoc.1.gz`, `pandoc-lua.1.gz` and `pandoc-server.1.gz` in its mise install root while `man pandoc` reports no manual entry; `fzf`, `just`, `zoxide`, `pastel` and `gh` ship pages too (`gh` ships 220).
Evidence behind ADR-0015: 588 PATH symlinks resolve in 1.82s against 1.96s to classify 5504 manpages, over a fixed 0.84s of interpreter and import startup -- so inverting enumeration buys capability, not speed, and the speed argument should not be made again.
`CARGO_HOME` is `~/.local/share/cargo` here, not `~/.cargo`, and held no `.crates2.json` at the time ADR-0015 was written -- Stage 4 verifies `CargoProvider` against a real `cargo install hexyl` made since, see above.
