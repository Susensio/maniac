# Implementation State

## Candidate discovery

`maniac list-missing` lists pages missing from the selected binary directory.
`--include-candidates` additionally scans the active manpath once (via `manpath`), reading at most 8 KiB per plain or compressed page to identify Help2man-generated pages by their marker, and deduplicates localized copies by `(name, section)` down to the shortest path.
There is no command-name exclusion, including for `help2man` itself.

A candidate row is actionable through either a source-backed executable (a local project, UV tool metadata, or a Mise installation) or, lacking that, a subcommand probe: `--include-candidates` runs every source-unknown candidate that is on `$PATH` with `--help` (one-second timeout) and extracts its top-level subcommands.
Candidates whose manpage exists but whose executable is unavailable are skipped before spawning a process, so a stale page like `pysetup3.12`'s doesn't produce a noisy `pysetup3.12 --help` attempt; candidates with no subcommands are omitted unless source-backed.
Source-unknown subcommand rows show `Unknown`; known remote sources render as OSC 8 hyperlinks without an underline.

Candidate discovery is deliberately stricter than general `docs`/`generate` repository resolution: it requires an installed executable plus installation-derived evidence, and never turns a bare command name into a repository through the Mise registry.
The cached official registry matched `mise registry --json` for all 778 shared GitHub/Aqua short names on the development system with zero conflicting selections, and Mise-config matching is exact for repository tails and `filter_bins` (no substring matches).
See `docs/BACKLOG.md` for the still-open repository-resolution and package-provenance work.

## Decisions

ADR-0005 records the removal of the Mise executable dependency in favour of optional local configuration and the official registry archive.
ADR-0010 is the current candidate policy (supersedes ADR-0009): generic Help2man classification and deduplication, installation-tied sources, no name-only repository attribution, default one-second subcommand probes for available unbacked commands, and `Unknown` source presentation for subcommand-backed rows.

## Verification performed

`just check` (Ruff format, Ruff lint, `ty`, pytest) passes.
An independent audit passed the default-probe, PATH-guard, source-attribution, and ADR-0010 criteria.
On the development system, `config.guess`, `config.sub`, and `pysetup3.12` are confirmed off `$PATH` and are skipped without being invoked.

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

## Working-tree note

The repository contains uncommitted, related work across candidate discovery, Mise registry discovery, configuration, LLM integration, documentation, and tests.
Do not reset or discard unrelated changes when continuing this work.
