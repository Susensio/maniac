# MANIAC

> **MAN**page **I**ntelligent **A**rtificial **C**reator  
> *Instant, authoritative Unix manual pages for any CLI tool on your system.*

---

## Why MANIAC?

Modern command-line tools written in Rust, Go, Python, and Zig are faster and more capable than ever. You install dozens of them with `mise`, `uv`, `cargo`, or `brew`. 

Then you type:

```bash
$ man uv
No manual entry for uv

$ man hx
No manual entry for hx

$ man howdoi
No manual entry for howdoi
```

Most modern utilities ship without standard Unix manual pages. Instead, you're forced to switch contexts: opening web browsers, scrolling through sprawling online docs, or pipe-grepping `--help` strings across nested subcommands.

**MANIAC solves this completely.** It inspects any binary on your system, recursively crawls its entire subcommand tree, discovers its upstream documentation, and uses an LLM to synthesize a gold-standard Unix manual page (`.1` roff) formatted with Pandoc and installed straight into your local `man` path.

---

## The Synthesis Pipeline

```
  ┌──────────────────┐       ┌────────────────────────┐
  │ Recursive Help   │       │ Upstream Documentation │
  │ Tree Scraper     │       │ Discovery (mise / git) │
  └─────────┬────────┘       └───────────┬────────────┘
            │                            │
            └─────────────┬──────────────┘
                          ▼
             ┌──────────────────────────┐
             │ Prioritized Context Pool │
             └────────────┬─────────────┘
                          ▼
             ┌──────────────────────────┐
             │ AI Manual Crafting Engine│
             │ (Domain Ontology, Flags, │
             │  Keybinds, Subsystems)   │
             └────────────┬─────────────┘
                          ▼
             ┌──────────────────────────┐
             │ Pandoc roff Compiler     │
             │ & Quality Judge (0-100)  │
             └────────────┬─────────────┘
                          ▼
             ~/.local/share/man/man1/<tool>.1
```

1. **Recursive Subcommand Crawler**: Traverses multi-level CLI trees (`uv pip compile`, `git remote add`) with cycle protection, pager suppression, and ANSI stripping.
2. **Dynamic Repository Discovery**: Resolves upstream GitHub sources and local clones dynamically via `mise` configuration, `uv tool` metadata, and symlink inspection—without hardcoded lists.
3. **Prioritized Doc Extraction**: Extracts key reference material, core concepts, and keybindings while ignoring build scripts, CI configs, and changelogs.
4. **Unix Manual Synthesis**: Structures descriptions around foundational domain entities (sessions, workspaces, buffers, modes), separates global options from subcommands, styles arguments rigorously, and adds annotated workflow examples.
5. **Compilation & Installation**: Compiles to standard `roff` via Pandoc and installs to `~/.local/share/man/man1/`.
6. **Automated Quality Evaluation**: Built-in LLM-as-a-Judge pipeline scoring generated manuals against a strict 100-point Unix documentation rubric.

---

## Prerequisites

- **[Python](https://www.python.org/)**: 3.12+
- **[uv](https://github.com/astral-sh/uv)**: Fast Python package and tool runner
- **[pandoc](https://pandoc.org/)**: Document converter for Markdown &rarr; roff compilation
- **[agy](https://github.com/google-antigravity/antigravity-cli)**: Local LLM execution sandbox
- **[git](https://git-scm.com/)**: Repository cloning and remote URL inspection
- **[mise](https://mise.jdx.dev/)** *(optional)*: Dynamic upstream repository discovery via mise registry and tool aliases

---

## Installation

Install `maniac` as a standalone CLI tool in your `$PATH` using `uv`:

```bash
# Install directly from local repository
uv tool install .

# Or install in editable mode for local development
uv tool install --editable .
```

### Shell Completions

Install autocompletion for your active shell (Fish, Zsh, Bash):

```bash
maniac --install-completion
```

Or print the raw completion script to inspect or redirect to a custom path:

```bash
maniac --show-completion
```

---

## Quickstart

Run commands using the installed `maniac` binary (or via `uv run maniac`):

### 1. Generate & Install a Manpage

Generate a complete manpage and install it to your user manual directory:

```bash
# Generate and view compiled manpage
uv run maniac generate hx --install
uv run maniac generate uv --install
uv run maniac generate howdoi --install

# Now use standard man immediately
man hx
man uv
man howdoi
```

### 2. Discover Binaries Missing Manpages

Scan your local binary directory (`~/.local/bin`) to find everything you have installed that lacks a manual entry:

```bash
uv run maniac list-missing
```

### 3. Batch Generation

Generate and install manual pages for your entire toolkit in one command:

```bash
uv run maniac batch hx uv howdoi glow ruff bat --install
```

### 4. Evaluate Manual Quality

Run the automated LLM-as-a-Judge evaluation against the extracted documentation context:

```bash
# Grade the generated manual (0-100 score with category breakdown)
uv run maniac eval howdoi
uv run maniac eval uv --min-score 80
```

```text
        Quality Evaluation: howdoi (98/100)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━┓
┃ Rubric Category                        ┃  Score ┃ Max ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━┩
│ Domain Ontology & Architecture         │     20 │  20 │
│ Command & Flag Correctness & Coverage  │     20 │  20 │
│ Flag & Command Formatting              │     19 │  20 │
│ Subsystem Grouping                     │     20 │  20 │
│ Environment, Reference & Examples      │     19 │  20 │
├────────────────────────────────────────┼────────┼─────┤
│ Total Score                            │     98 │ 100 │
│ Status                                 │ PASSED │     │
└────────────────────────────────────────┴────────┴─────┘
```

### 5. Manage Installed Manpages

List all MANIAC-synthesized manpages or safely uninstall them:

```bash
# List all generated manpages with generation date, model, and backup status
uv run maniac list

# Uninstall an installed manpage (automatically restoring vendor backups if present)
uv run maniac uninstall howdoi

# Uninstall and purge generated Markdown source and intermediate context files
uv run maniac uninstall howdoi --purge
```

### 6. Inspect Subcommands or Upstream Docs

Debug and inspect extracted CLI help trees and upstream doc files independently:

```bash
# Inspect the scraped help tree for deep subcommands
uv run maniac crawl uv pip

# Discover the upstream repository and inspect extracted reference files
uv run maniac docs hx
```

---

## Development

Run tests, formatting, and linting with [`just`](https://github.com/casey/just):

```bash
# Run all verification checks (linter, formatting, test suite)
just check

# Run pytest unit and integration tests
just test

# Fix linting and format codebase
just format
just lint-fix
```
