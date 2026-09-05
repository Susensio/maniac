# Implementation State

## Configuration and LLM access

ADR-0011 removed the `backend` setting, both model alias tables, and the `agy` subprocess integration.
A model is now a literal LiteLLM identifier; there is no translation layer.
`maniac/defaults.toml` ships in the wheel holding a flat `[providers]` table mapping provider to default model, plus `[limits]` and `[timeouts]` tables for resource and timeout configuration.
User settings live in `$XDG_CONFIG_HOME/maniac/config.toml` with optional keys `provider`, `model`, and `reasoning_effort`; `.env` continues to hold credentials.
A leftover `config.yaml` with no `config.toml` present raises an error naming the format change.
`pyyaml` was dropped as a dependency.

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

`manpages.find_repo_manpage(repo_dir, binary_name)` globs a repo's root and its `man`/`doc`/`docs` trees for `<bin>.[1-9]` then `<bin>-*.[1-9]` (mirroring the glob `~/.config/mise/tasks/system-install` uses), rejecting help2man-generated hits.
An exact `<bin>.<section>` match always wins over a `-*` subcommand variant -- naive lexical sorting returns `fzf-tmux.1` before `fzf.1` since `-` sorts before `.`.
`docs.discover_repo_manpage` resolves (cloning if needed) through the same cache path `fetch_and_extract_docs` uses, via `docs.resolve_repo_dir`, then delegates to `find_repo_manpage`.

Confirmed against real repos: finds `fzf.1` at `junegunn/fzf` and `tmux.1` at the root of `tmux/tmux`.
Cannot find a page for `eza-community/eza` or `sharkdp/bat`, whose manpages are release-time-generated assets never committed to the tree; see `docs/BACKLOG.md` for that gap and the separate `tmux`/`tmux-builds` docs-repo mismatch this testing surfaced.

Not wired into any command yet -- `status` and `run_pipeline` both still treat every candidate as needing an LLM-generated page; see `docs/BACKLOG.md`.

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
With no arguments it reads `classification.collect_facts()` (never recomputing classification) and keeps pages with a resolvable source or that MANIAC already manages; with tool names it reports exactly those, unfiltered -- classification facts only exist for pages the manpath scan can see, so a named tool with no installed page at all reports nothing, a gap left open rather than reimplementing a bin-dir scan ADR-0013 deliberately removed.
`--candidates` filters further to `candidates.select_candidate(...) is SELECTED` against `Config.min_words_per_flag`, never a CLI-exposed threshold, per ADR-0014.
Columns are observations only (word count, flag-entry count, ownership, source), never a verdict.
Redirected to anything but a terminal, `status` prints bare tool names -- one per line, deduplicated across sections, via `print()` rather than the Rich console -- which is what makes `maniac status --candidates | xargs maniac generate` and `maniac generate $(maniac status --candidates)` work; `--names` forces the same output on a real terminal.

`generate` now installs by default, with `--no-install` to stop after compiling -- it and the removed `generate-missing` disagreeing on this default for the same pipeline was the bug named in ADR-0013.
`generate` with zero tool names exits 0 quietly rather than raising Typer's missing-argument error, since a `$(maniac status --candidates)` expansion can legitimately be empty.

Verified for real on the development system: `maniac status --candidates` selects `gum`, `gh`, `pastel`, `just` and excludes `usage`, `aichat`, `tmux`, `bat`, `fish-lsp`, matching ADR-0014's numbers; `maniac status --candidates | cat` prints the four names bare, one per line.

## Verification performed

`just check` (Ruff format, Ruff lint, `ty`, pytest) passes with 232 tests, exit 0.
