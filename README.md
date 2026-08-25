# maniac

An automated pipeline that scrapes recursive CLI `--help` trees, dynamically discovers upstream repository documentation via `mise` / `uv` / Git, synthesizes conceptually grouped Unix manual pages using an LLM, compiles them into standard `roff` manpages via Pandoc, and provides an LLM-as-a-Judge quality evaluation loop.

---

## Features

- **Recursive Subcommand Crawler**: Automatically explores `--help` trees for multi-level CLIs (e.g. `uv pip compile`), stripping pagers and ANSI color codes while preventing infinite recursion loops.
- **Dynamic Repository Discovery**: Infers upstream source repositories dynamically from local `mise` configurations, `mise registry`, `uv tool` metadata, and Git remotes without hardcoded tables.
- **Prioritized Documentation Extraction**: Extracts and ranks key user documentation (`README.md`, `reference/cli.md`, `docs/concepts/*.md`, `manual/`) while skipping internal noise (contributing guides, changelogs, benchmarks).
- **Elite Unix Manual Synthesis**: Generates manpages modeled after `tmux(1)` and `git(1)` (foundational domain concepts upfront, categorized subsystem headers, Pandoc definition lists, exit statuses, and realistic workflow examples).
- **Pandoc Compilation & Installation**: Compiles Markdown manpages to `.1` roff format and installs them to `~/.local/share/man/man1/`.
- **LLM-as-a-Judge Quality Evaluator**: Automated 0-100 rubric grader evaluating domain ontology, flag formatting, subsystem grouping, reference completeness, and workflow examples.
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

### 2. Quality Evaluation with LLM-as-a-Judge

Evaluate the quality of a generated manpage against its reference context using the automated rubric:

```bash
uv run maniac eval howdoi
uv run maniac eval uv --min-score 80
```

### 3. Inspect Executables Missing Manpages

Scan `~/.local/bin` to find binaries that currently lack manual entries:

```bash
uv run maniac list-missing
```

### 4. Crawl CLI Subcommands & Help

Recursively crawl `--help` output for any CLI command:

```bash
uv run maniac crawl git
uv run maniac crawl uv pip
```

### 5. Fetch & Inspect Extracted Documentation

Discover the upstream repository and inspect the extracted documentation files:

```bash
uv run maniac docs uv
uv run maniac docs hx
```

### 6. Batch Generation

Generate manpages for multiple tools sequentially:

```bash
uv run maniac batch howdoi uv hx --install
```

### 7. Install Globally via `uv tool`

Install `maniac` into your local `PATH`:

```bash
# Install tool from current directory
uv tool install .

# Now run maniac directly anywhere
maniac list-missing
maniac generate howdoi --install
maniac eval howdoi
```

---

## Project Structure

```text
maniac/
├── maniac/
│   ├── cli.py                  # Typer CLI application entry point
│   ├── config.py               # Central configuration, paths, model aliases & limits
│   ├── eval.py                 # LLM-as-a-Judge & deterministic quality evaluation
│   ├── exceptions.py           # Exception hierarchy (CrawlerError, DiscoveryError, etc.)
│   ├── models.py               # Shared data transfer objects
│   ├── crawler.py              # CLI help tree & subcommand crawler
│   ├── discovery.py            # Mise / git / uv repository discovery
│   ├── docs.py                 # Git repository doc extractor & ranker
│   ├── extractor.py            # Subcommand regex parser
│   ├── compiler.py             # Pandoc roff compiler & installer
│   ├── llm.py                  # Sandbox LLM execution engine
│   ├── prompts.py              # System prompt & context template builder
│   ├── pipeline.py             # Orchestration pipeline
│   └── templates/              # External prompt template files
│       ├── system_prompt.md    # Base synthesis prompt template
│       └── eval_prompt.md      # Evaluation rubric prompt template
├── data/
│   ├── intermediate/           # Extracted context and prompts per tool
│   ├── manpages/               # Generated .1.md and compiled .1 manpages
│   └── repos/                  # Cached shallow repository clones
├── tests/                      # Pytest test suite (48 unit & integration tests)
├── Justfile                    # Developer workflows (test, lint, format, check)
└── pyproject.toml              # Project configuration and metadata
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
