# MANIAC

> **MAN**page **I**ntelligent **A**rtificial **C**reator  
> *Instant, authoritative Unix manual pages for any CLI tool on your system.*

> **Being redesigned.** [`docs/CONTRACT.md`](docs/CONTRACT.md) states how maniac decides, and the smaller command set it is moving to; [`docs/STATE.md`](docs/STATE.md) tracks which parts are live. This README describes the commands as they work today.

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
- **[pandoc](https://pandoc.org/) 3.1.10+**: Document converter for Markdown &rarr; roff compilation. Older releases write option dashes that `man` shows as Unicode hyphens, so maniac refuses them; Ubuntu 24.04 ships 3.1.3, so install the `.deb` from [pandoc's releases](https://github.com/jgm/pandoc/releases).
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

### 1. Install Manpages

`install` tries the most authoritative source first: a manpage already shipped inside the tool's own install root, then one fetched from its upstream repository at the matching version, then LLM synthesis from `--help` and repository docs.
It reports which one answered:

```bash
$ maniac install pandoc hx
pandoc   shipped page (3.10.2)   [no synthesis]
hx       generated from --help + repo docs
```

`--no-synthesize` restricts this to the first two tiers and never calls an LLM.

A page maniac already installed that `list` reads `ok` is left alone (`npm   already up to date (10.9.4); --force reinstalls it`), so naming it again costs nothing; an `outdated` or `unknown` page is reinstalled. If the upstream check cannot complete (network or git), `install` says so rather than reporting that no page exists.

Under each tool, `install` prints the binary the page documents and where its version came from: the global copy your login shell runs from `$HOME`, since a manpage is global. When the shell you ran it from would run a different copy (an activated venv, a Mise project pin), it says so:

```bash
$ cd ~/proj && . .venv/bin/activate && maniac install ruff
ruff     generated from --help + repo docs
  documents ~/.local/bin/ruff (uv, 0.6.9)
  this shell runs ~/proj/.venv/bin/ruff instead
```

Where `install` would refuse, `--force` installs anyway and prints a `forced:` line for each refusal it overrode. That covers a tool with no global copy (only a venv or a project has it: the copy this shell runs is documented, recorded, and checked by `list` from then on), a system package's tool (its package normally maintains its page), and a page maniac did not install sitting where it would write (kept as a backup that `uninstall` restores).

Before synthesis, MANIAC reports how many commands, subcommands, and repository documents it found, which repository it used, and whether those documents matched the installed version.
Root `--help` alone is enough to generate a page when no better source exists, but MANIAC warns that the source material is limited.
Repository documentation alone can also be used when the help crawl fails; synthesis stops only when neither source provides usable material.
A failed `--help` invocation is accepted as documentation only when its combined output contains a recognizable `usage:` line, covering tools such as tmux that write usage to stderr and exit nonzero without treating a bare option error as help.

For `scan`, upstream repository checks inspect the exact version-matched tree without cloning or checking out a worktree, using cached Git tree objects and materializing only a selected page.
For GitHub sources, MANIAC also inspects bounded, likely release artifacts independently, validates their archive contents rather than trusting artifact names, and can therefore discover release-only pages such as eza's.
It never reads handcrafted Mise `extra_assets` entries.
A validated bundle retains and installs every valid companion manpage across sections.
Every managed manpath entry is a manifest-tracked symbolic link. A verified vendor page links directly to its provider target, using Mise's `latest` alias only when it resolves to the executable's exact install root. Repository and synthesized pages link to a MANIAC-owned durable copy rather than the disposable cache. The manifest records their tier, source, version, checksum, destination, and any displaced-page backup. Uninstall removes a manifest-owned page and restores that backup even when its bytes changed since install, warning about the change rather than refusing; a retargeted or dangling link is no longer provably MANIAC's page and is left in place instead. Uninstall operates on the whole upstream release, so removing the primary name removes every companion page installed with it too; naming a companion instead while its primary is still installed refuses and redirects to the primary's name (ADR-0053), removing the companion outright only once the primary entry is already gone.
The default cache is `$XDG_CACHE_HOME/maniac/repos` (normally `~/.cache/maniac/repos`), with bare filtered Git objects under `git/`, selected pages under `manpages/`, release responses and assets under `releases/`, and tag/probe decisions under `upstream/`.
Positive versioned results persist; definitive misses expire after five minutes, and transient network failures are not cached as misses.
`scan` uses no sparse checkout; sparse checkouts are reserved for synthesis and limited to documentation paths.

```bash
# Install for a single tool
maniac install hx

# Install for multiple tools at once
maniac install uv howdoi glow ruff bat

# Never call an LLM: install root or repository only
maniac install pandoc --no-synthesize

# Now use standard man immediately
man hx
man uv
man howdoi
```

### 2. Your Pages: `list` and `update`

`list` shows the pages maniac installed: the version each documents, the version installed now, and its state. Pages maniac did not install are never listed here (`scan` shows those).

```bash
$ maniac list
Tool    State     Documents  Installed  Source
cowsay  unknown   -          1.5.0      maniac
ruff    outdated  0.6.8      0.6.9      maniac
  cowsay: no version was recorded for this page
```

A page is *ok* only when both versions are known and equal, *outdated* when they differ, and *unknown* when either is missing: no recorded version, a binary that cannot report one, or a pinned copy that is gone. `--outdated` and `--unknown` narrow to one state; piped, `list` prints bare names.

`update` reinstalls every *outdated* page maniac installed, and nothing else:

```bash
maniac update            # every outdated page
maniac update ruff       # just this one, saying why if it is left alone
```

An *unknown* page is left alone until you name it to `maniac install`. A page `--force` pinned to a non-global copy is reinstalled for that copy. Each tool commits on its own, so an interrupted update resumes by running it again. `update` takes `--model`, `--no-synthesize` and `--dry-run` as `install` does.

### 3. Your Other Tools: `scan`

`scan` discovers the tools maniac has no page for across your login `$PATH`, best effort, and reports the state of each one's page: *ok*, *unknown*, *outdated*, *available* (a page can be had without LLM synthesis), *missing* (no free page is known). Use `--unknown`, `--outdated`, `--available`, or `--missing` to select a state.
The table carries four columns: Tool, State, Source, and Upstream.
Source reports where the page came from: `shipped` for a page installed with the tool itself, `upstream` for one fetched from its repository, `generated` for one an LLM wrote, and `system` for another package's page already on the manpath.
The Source keyword links to the exact local page for `shipped`, `system`, and `generated`; for GitHub sources, `upstream` links to the version-pinned repository file or release asset that supplied it, never MANIAC's cache. Upstream links to the repository itself. Other Git hosts remain plain Source text until MANIAC has an exact-file URL adapter for that host.

```bash
maniac scan              # every tool an installer claims and maniac has no page for
maniac scan hx uv bat    # exactly these, maniac's own included
```

Nothing is left out silently: unnamed, `scan` ends by counting every binary it passed over, and why.

```text
Not shown: 312 more on your $PATH
     14 have a page from maniac: `maniac list`
     23 no installer claims: `maniac why <tool>`
    274 system tools, whose packages ship their pages
      1 mise shims that run nothing from $HOME
```

The count goes to stderr, so piped output stays bare names.

Piped, `scan` prints bare tool names, so installing every page that is missing is one line:

```bash
maniac scan --available --missing | xargs maniac install
```

`install` with zero tool names exits quietly, so an empty expansion is harmless.

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

`list` (section 2) shows every page MANIAC installed, with the source its content came from: a page shipped with the tool reads `shipped`, one from its repository `upstream`, and one an LLM wrote `generated`.
Uninstall safely restores any vendor backup:

```bash
# Uninstall an installed manpage (automatically restoring vendor backups if present)
maniac uninstall howdoi

# Uninstall and purge generated Markdown source and intermediate context files
maniac uninstall howdoi --purge
```

### 6. Ask Why

`why <tool>` explains every decision maniac makes about a tool, and what `install` would do with it:

```bash
$ cd ~/proj && . .venv/bin/activate && maniac why ruff
PATH     read from `/bin/bash -lc` started in $HOME: 14 directories
         left out: ~/proj/.venv/bin, an activated virtualenv (VIRTUAL_ENV) (holds a copy of this tool)
Binary   login $PATH reaches ~/.local/bin/ruff
         installed by uv as ruff (0.6.9), root ~/.local/share/uv/tools/ruff
         this shell runs ~/proj/.venv/bin/ruff instead
Page     `man` finds no page for it
Sources  shipped: none in ~/.local/share/uv/tools/ruff
         upstream: astral-sh/ruff at 0.6.9: no manpage
         generated: from its own --help (`why --help-text`) and astral-sh/ruff's docs (`why --docs`)
install  generated
```

`--help-text` also prints the `--help` output, subcommands included, that a generated page would be written from; `--docs` lists the repository documents it would use.

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
# Run all verification checks (linter, formatting, types, unit and integration tests)
just check

# Run the unit suite (no external tools needed)
just test

# Run the integration suite: needs pandoc 3.1.10+, groff, man-db, npm, uvx and network
just integration

# Fix lint issues and format code
just fix
```

Configuration lives in `~/.config/maniac/config.toml`; credentials go in
`~/.config/maniac/.env`. Packaged defaults ship in `maniac/defaults.toml`
and any of them can be overridden:

```toml
provider = "gemini"            # picked by API key if unset
model = "gemini/gemini-flash-latest"
reasoning_effort = "low"
```
