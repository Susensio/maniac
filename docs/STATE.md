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
Per-page stored facts are `word_count`, `tp_count`, `sections`, `has_examples_section`, plus path, existence, whether MANIAC authored it, and reachable sources for the tool.
There is deliberately no verdict, state vocabulary, or combined score — the verdict layer is deferred to real output per ADR-0012.
Generator origin covers help2man, Pod::Man, Pandoc, txt2man, DocBook XSL, po4a and cobra, identified by marker in the first 8 KiB of the roff source; generator origin is internal and not user-facing.

The cache lives under `$XDG_CACHE_HOME/maniac/` keyed on `(path, mtime, size)`, carries `CACHE_SCHEMA_VERSION`, and is discarded wholesale on a version mismatch.
A malformed row or corrupt cache file is treated as a miss rather than raising; a cache is an optimisation and corruption in one row should cost recomputing that row, not the entire cache.
A dump entry point exists at `python -m maniac.classification`.

Two decisions ADR-0012 did not settle: `.SS` subsections are not counted in `sections`, only top-level `.SH`; `has_examples_section` matches `EXAMPLES` or `USAGE` case-insensitively against exact section names, not substrings.

Evidence from a real run on the development system: 5504 pages classified; generator origins split as none 3741, Pod::Man 867, DocBook XSL 654, help2man 220, pandoc 17, txt2man 4, po4a 1; 15 pages resolved a reachable source under the ADR-0008 installation-tied rule; 2 pages are MANIAC-authored.
Known limitation: `tmux.1` is BSD mdoc, so its `tp_count` is 0 and `sections` empty while its `word_count` is correct; `docs/BACKLOG.md` carries that item.

## Repo-shipped manpage detection

`manpages.find_repo_manpage(repo_dir, binary_name)` globs a repo's root and its `man`/`doc`/`docs` trees for `<bin>.[1-9]` then `<bin>-*.[1-9]` (mirroring the glob `~/.config/mise/tasks/system-install` uses), rejecting help2man-generated hits.
An exact `<bin>.<section>` match always wins over a `-*` subcommand variant -- naive lexical sorting returns `fzf-tmux.1` before `fzf.1` since `-` sorts before `.`.
`docs.discover_repo_manpage` resolves (cloning if needed) through the same cache path `fetch_and_extract_docs` uses, via `docs.resolve_repo_dir`, then delegates to `find_repo_manpage`.

Confirmed against real repos: finds `fzf.1` at `junegunn/fzf` and `tmux.1` at the root of `tmux/tmux`.
Cannot find a page for `eza-community/eza` or `sharkdp/bat`, whose manpages are release-time-generated assets never committed to the tree; see `docs/BACKLOG.md` for that gap and the separate `tmux`/`tmux-builds` docs-repo mismatch this testing surfaced.

Not wired into any command yet -- `list-missing --include-candidates` and `run_pipeline` both still treat every candidate as needing an LLM-generated page; see `docs/BACKLOG.md`.

## Installed-vs-generated manpage comparison

`maniac compare <tool>` locates the installed page via `manpages.find_installed_manpage_path` (`man -w <tool>`), reads it in full with `manpages.read_manpage_source` (same decompression as `_read_prefix`, without its 8 KB cap), and passes both it and MANIAC's generated Markdown to `evaluation.judge.compare_manpages`.
The generated page is scored with `evaluate_manpage` (deterministic Markdown-structure checks plus the LLM judge); the installed page, being raw roff rather than Markdown, is scored with `run_llm_judge` alone.
A third call (`run_comparison_judge`, prompt in `maniac/templates/compare_prompt.md`) judges both pages head-to-head against the same reference context and returns a prose verdict, not just two independent scores.
`parse_evaluation_json`'s JSON-extraction/repair logic is now shared through `_extract_json_object`, reused by the new `parse_comparison_json`.

Not yet run against a live LLM -- covered by unit tests only (`tests/test_compare.py`, `tests/test_manpages.py`), all with `run_llm_synthesis` mocked.
A staged `fzf` markdown/context pair is ready at `~/.local/share/maniac/manpages/fzf.1.md` / `~/.local/state/maniac/intermediate/fzf_context.md` for whenever the API is available; see `docs/BACKLOG.md`.

## Test-suite note

`tests/test_cli.py`'s `_plain_console` fixture now pins console width as well as colour.
Assertions matching rendered Rich output were failing or passing according to the ambient terminal width and `--basetemp` depth because `Rich.console` wraps to the terminal size, breaking path assertions mid-word; pinning width to 400 stabilizes the assertions.
The fixture carries a `TODO:` naming ADR-0013's `cli.py` split as the trigger for removing the coupling altogether.
`docs/BACKLOG.md` carries the item to stop asserting on prose and instead assert on result structures.

## Verification performed

`just check` (Ruff format, Ruff lint, `ty`, pytest) passes with 194 tests, exit 0.
