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

## Prerequisites

- **[Python](https://www.python.org/)**: 3.12+
- **[uv](https://github.com/astral-sh/uv)**: Fast Python package and tool runner
- **[pandoc](https://pandoc.org/)**: Document converter for Markdown &rarr; roff compilation
- **LiteLLM-compatible API key**: A provider-native key, such as `GEMINI_API_KEY`, for the default API backend
- **[agy](https://github.com/google-antigravity/antigravity-cli)** *(optional)*: Explicit fallback backend
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

### LLM Backend

MANIAC uses LiteLLM by default and accepts any provider-qualified LiteLLM model ID.
It reads settings from `$XDG_CONFIG_HOME/maniac/config.yaml` (usually `~/.config/maniac/config.yaml`):

```yaml
model: gemini/gemini-3.5-flash
backend: litellm
reasoning_effort: low
```

Only `model` is required; `backend` and `reasoning_effort` are optional.
It autoloads credentials from the neighbouring `.env` without replacing variables already set in your shell.
For example, `~/.config/maniac/.env` can hold either Gemini or Anthropic credentials:

```dotenv
GEMINI_API_KEY='your-api-key'
ANTHROPIC_API_KEY='your-api-key'
```

You can keep credentials for several providers in that one file and select one at a time with `model`.
`MANIAC_LLM_API_KEY` remains available when you want to override LiteLLM's provider-native credential lookup.
Environment variables, such as `MANIAC_MODEL`, override `config.yaml` settings.

For Gemini models, `MANIAC_REASONING_EFFORT` defaults to `low` to conserve API quota.
Other models omit it unless you set it explicitly.
Set it to `high` only when your selected provider supports a higher-reasoning mode.

The legacy `agy` integration remains available as an explicit fallback:

```bash
MANIAC_LLM_BACKEND=agy maniac generate <tool>
```

---

## Quickstart

### 1. Generate & Install Manpages

Generate complete manpages and install them to your user manual directory:

```bash
# Generate for a single tool
maniac generate hx --install

# Generate for multiple tools at once
maniac generate uv howdoi glow ruff bat --install

# Now use standard man immediately
man hx
man uv
man howdoi
```

### 2. Discover Missing or Help-Derived Manpages

Scan your local binary directory (`~/.local/bin`) to find everything you have installed that lacks a usable manual entry:

```bash
maniac list-missing
```

Include system-wide help-derived pages that MANIAC can improve from an installed source or deeper subcommand help.
This scans the active manpath, resolves each candidate's installed source when possible, and makes one short local help probe for source-unknown commands.
It does not call an LLM:

```bash
maniac list-missing --include-candidates
```

### 3. Automatically Generate Missing Manpages

Find all installed executables that lack manpages and automatically synthesize and install them in one go:

```bash
maniac generate-missing
```

### 4. Evaluate Manual Quality

Run the automated LLM-as-a-Judge evaluation against the extracted documentation context:

```bash
# Grade the generated manual (0-100 score with category breakdown)
maniac eval howdoi
maniac eval uv --min-score 80
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
maniac list

# Uninstall an installed manpage (automatically restoring vendor backups if present)
maniac uninstall howdoi

# Uninstall and purge generated Markdown source and intermediate context files
maniac uninstall howdoi --purge
```

### 6. Inspect Subcommands or Upstream Docs

Debug and inspect extracted CLI help trees and upstream doc files independently:

```bash
# Inspect the scraped help tree for deep subcommands
maniac crawl uv pip

# Discover the upstream repository and inspect extracted reference files
maniac docs hx
```

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
2. **Dynamic Repository Discovery**: Resolves upstream GitHub sources and local clones dynamically via the cached [Mise registry](https://mise.jdx.dev/registry), optional local Mise configuration, `uv tool` metadata, and symlink inspection—without hardcoded lists or a Mise installation.
3. **Prioritized Doc Extraction**: Extracts key reference material, core concepts, and keybindings while ignoring build scripts, CI configs, and changelogs.
4. **Unix Manual Synthesis**: Structures descriptions around foundational domain entities (sessions, workspaces, buffers, modes), separates global options from subcommands, styles arguments rigorously, and adds annotated workflow examples.
5. **Compilation & Installation**: Compiles to standard `roff` via Pandoc and installs to `~/.local/share/man/man1/`.
6. **Automated Quality Evaluation**: Built-in LLM-as-a-Judge pipeline scoring generated manuals against a strict 100-point Unix documentation rubric.

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
