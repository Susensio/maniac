# maniac

An automated pipeline that scrapes recursive CLI `--help` trees, dynamically discovers upstream repository documentation via `mise` / `uv` / Git, synthesizes conceptually grouped Unix manual pages using an LLM, and compiles them into standard `roff` manpages via Pandoc.

---

## Features

- **Recursive Subcommand Crawler**: Automatically explores `--help` trees for multi-level CLIs (e.g. `uv pip compile`), stripping pagers and ANSI color codes while preventing infinite recursion loops.
- **Dynamic Repository Discovery**: Infers upstream source repositories dynamically from local `mise` configurations, `mise registry`, `uv tool` metadata, and Git remotes without hardcoded tables.
- **Prioritized Documentation Extraction**: Extracts and ranks key user documentation (`README.md`, `reference/cli.md`, `docs/concepts/*.md`, `manual/`) while skipping internal noise (contributing guides, changelogs, benchmarks).
- **Elite Unix Manual Synthesis**: Generates manpages following the structural standards of `tmux(1)` and `git(1)` (foundational domain concepts upfront, categorized subsystem headers, Pandoc definition lists, exit statuses, and realistic workflow examples).
- **Pandoc Compilation & Installation**: Compiles Markdown manpages to `.1` roff format and installs them to `~/.local/share/man/man1/`.
- **Intermediate Artifact Preservation**: Automatically saves intermediate extracted contexts and raw prompts to `data/intermediate/` for inspection and debugging.

---

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (recommended Python package manager)
- [pandoc](https://pandoc.org/) (for compiling Markdown to roff manpages)
- [agy](https://github.com/google-antigravity/antigravity-cli) / `~/bin/sandbox` (for LLM synthesis)

---

## Running with `uv`

### 1. Run Directly with `uv run`

Execute `maniac` commands directly without manually activating virtual environments:

```bash
# Generate a complete manpage (Markdown + compiled roff)
uv run maniac generate howdoi
uv run maniac generate uv
uv run maniac generate hx

# Generate and install directly to ~/.local/share/man/man1/
uv run maniac generate hx --install

# Dry-run generation (extracts CLI help & docs, skips LLM call)
uv run maniac generate git --dry-run
```

### 2. Inspect Executables Missing Manpages

Scan `~/.local/bin` to find binaries that currently lack manual entries:

```bash
uv run maniac list-missing
```

### 3. Crawl CLI Subcommands & Help

Recursively crawl `--help` output for any CLI command:

```bash
uv run maniac crawl git
uv run maniac crawl uv pip
```

### 4. Fetch & Inspect Extracted Documentation

Discover the upstream repository and inspect the extracted documentation files:

```bash
uv run maniac docs uv
uv run maniac docs hx
```

### 5. Batch Generation

Generate manpages for multiple tools sequentially:

```bash
uv run maniac batch howdoi uv hx --install
```

### 6. Install Globally via `uv tool`

Install `maniac` into your local `PATH`:

```bash
# Install tool from current directory
uv tool install .

# Now run maniac directly anywhere
maniac list-missing
maniac generate howdoi --install
```

---

## Project Structure

```text
maniac/
├── maniac/
│   ├── cli.py          # Typer CLI application entry point
│   ├── crawler.py      # Recursive CLI subcommand & help scraper
│   ├── discovery.py    # Dynamic tool & repository discovery (mise, git, uv)
│   ├── docs.py         # Prioritized repository doc extractor & ranker
│   ├── extractor.py    # Subcommand regex parser
│   ├── prompts.py      # Unix manual system prompt & context injector
│   ├── llm.py          # LLM synthesizer runner
│   ├── compiler.py     # Pandoc roff compiler & manpage installer
│   └── pipeline.py     # End-to-end orchestration pipeline
├── data/
│   ├── intermediate/   # Extracted context and prompts per tool
│   ├── manpages/       # Generated .1.md and compiled .1 manpages
│   └── repos/          # Cached shallow repository clones
├── tests/              # Pytest test suite (unit and integration tests)
├── Justfile            # Developer workflows (test, lint, format, check)
└── pyproject.toml      # Project configuration and metadata
```

---

## Development

Use `just` to run developer checks:

```bash
# Run all verification checks (linter, formatting, tests)
just check

# Run unit and integration tests
just test

# Format code and fix linter issues
just format
just lint-fix
```
