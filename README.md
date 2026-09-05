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
- **LiteLLM-compatible API key**: A provider-native key, such as `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENAI_API_KEY`
- **[git](https://git-scm.com/)**: Repository cloning and remote URL inspection

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

### LLM Configuration

MANIAC calls LiteLLM directly and accepts any provider-qualified LiteLLM model ID (for example `gemini/gemini-flash-latest`).
It packages a default model per provider; nothing needs configuring if exactly one provider's API key is in your environment.

Optional settings live in `$XDG_CONFIG_HOME/maniac/config.toml` (usually `~/.config/maniac/config.toml`):

```toml
provider = "anthropic"
model = "anthropic/claude-sonnet-4-6"
reasoning_effort = "low"
```

All three keys are optional.
Setting `provider` alone picks that provider's packaged default model; setting `model` pins an exact identifier and takes priority over `provider`.
`reasoning_effort` is sent to LiteLLM only for models that support it.

MANIAC autoloads credentials from the neighbouring `.env` without replacing variables already set in your shell.
For example, `~/.config/maniac/.env` can hold credentials for several providers at once:

```dotenv
GEMINI_API_KEY='your-api-key'
ANTHROPIC_API_KEY='your-api-key'
```

With no `model`/`provider` configured, MANIAC picks the first provider (in packaged declaration order: Gemini, Anthropic, OpenAI) whose API key is present in the environment.
Zero keys present is a hard error; two or more prints one line to stderr naming the provider chosen and how to pin it.
`MANIAC_LLM_API_KEY` overrides LiteLLM's provider-native credential lookup.
`MANIAC_MODEL` and `MANIAC_REASONING_EFFORT` are used when `config.toml` sets no `model`/`reasoning_effort`; an explicit `--model` argument and `config.toml` both outrank them.

---

## Quickstart

### 1. Generate & Install Manpages

`generate` compiles a manpage and installs it straight into your user manual directory; pass `--no-install` to stop after compiling:

```bash
# Generate and install for a single tool
maniac generate hx

# Generate for multiple tools at once
maniac generate uv howdoi glow ruff bat

# Compile only, without touching your man path
maniac generate hx --no-install

# Now use standard man immediately
man hx
man uv
man howdoi
```

### 2. Find Tools MANIAC Can Act On

`status` reports, with no arguments, every tool with a source MANIAC can reach plus every tool it already manages.
Columns are observations only -- word count, flag-entry count, page ownership, discovered source -- never a verdict:

```bash
maniac status
```

Name tools explicitly to report on exactly those, regardless of source or ownership:

```bash
maniac status hx uv bat
```

`--candidates` narrows the listing to pages MANIAC's internal heuristic flags as worth improving.
The heuristic and its threshold are internal (tunable via `config.toml`, never a CLI flag) — the columns above are what make a borderline page visible even when it isn't selected:

```bash
maniac status --candidates
```

### 3. Bulk-Generate via Piping

There is no bulk generation command: piping `status`'s output into `generate` is the bulk path.
Redirected to anything other than a terminal, `status` prints bare tool names, one per line, with no table, colour, or header:

```bash
maniac status --candidates | xargs maniac generate
# or, equivalently
maniac generate $(maniac status --candidates)
```

`generate` with zero tool names exits quietly, so an empty expansion is harmless.

### 4. Evaluate Manual Quality

Run the automated LLM-as-a-Judge evaluation against the extracted documentation context:

```bash
# Grade the generated manual (0-100 score with category breakdown)
maniac eval howdoi
maniac eval uv --min-score 80

# Judge the generated manual head-to-head against the one already installed
maniac eval howdoi --against-installed
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

`status` doubles as the inventory view -- a tool MANIAC manages is marked in its Owner column.
Uninstall safely restores any vendor backup:

```bash
# Uninstall an installed manpage (automatically restoring vendor backups if present)
maniac uninstall howdoi

# Uninstall and purge generated Markdown source and intermediate context files
maniac uninstall howdoi --purge
```

### 6. Inspect Subcommands or Upstream Docs

Debug and inspect extracted CLI help trees and upstream doc files independently, under the `source` group:

```bash
# Inspect the scraped help tree for deep subcommands
maniac source crawl uv pip

# Discover the upstream repository and inspect extracted reference files
maniac source docs hx
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
