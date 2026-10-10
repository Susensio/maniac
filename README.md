# MANIAC

> **MAN**page **I**ntelligent **A**rtificial **C**reator
> *Manpages for the tools you install, kept matching their versions.*

---

## Why

You install dozens of command-line tools with `mise`, `uv`, `cargo`, `npm` or `brew`. Then:

```bash
$ man uv
No manual entry for uv
```

Many modern tools ship no manual page, or ship one that `man` cannot find. maniac gives each the best page there is, in this order:

| Source | What it is |
|---|---|
| `shipped` | The page the tool itself installed, linked where `man` finds it. |
| `upstream` | The project's own page for exactly the installed version. |
| `generated` | Written by an LLM from the tool's `--help`, subcommands included, and its project's documentation. |

Only `generated` costs a model call, and `--no-generate` forbids it.

## What you can count on

maniac follows four rules ([`docs/CONTRACT.md`](docs/CONTRACT.md) has them in full):

1. **It changes only the pages it installed, or the tools you name.** A page another package owns is never touched, unless you name its tool and pass `--force`; then it is kept as a backup that `remove` puts back.
2. **It documents the tool your login shell runs from `$HOME`,** because a manpage is global. When the shell you are in would run another copy (an activated venv, a project's mise pin), it says so.
3. **Every skip is counted, and every decision can be explained.** `scan` ends with what it passed over, and why; `why <tool>` prints the whole chain.
4. **Missing evidence reads `unknown`.** A page is `ok` only when evidence says it documents the installed version.

---

## Install maniac

Needs:

- **[Python](https://www.python.org/) 3.12+** and **[uv](https://github.com/astral-sh/uv)**
- **[pandoc](https://pandoc.org/) 3.1.10+**, to compile generated pages to roff. Older releases write option dashes that `man` shows as Unicode hyphens, so maniac refuses them; Ubuntu 24.04 ships 3.1.3, so install the `.deb` from [pandoc's releases](https://github.com/jgm/pandoc/releases).
- **[git](https://git-scm.com/)**, to read upstream repositories.
- **An LLM API key** for generated pages, such as `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`. Shipped and upstream pages need none.

```bash
uv tool install .               # from a clone of this repository
uv tool install --editable .    # or editable, to work on maniac
maniac --install-completion     # optional: completion for fish, zsh or bash
```

### LLM configuration

maniac calls LiteLLM directly and accepts any provider-qualified LiteLLM model ID (for example `gemini/gemini-flash-latest`).
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

maniac autoloads credentials from the neighbouring `.env` without replacing variables already set in your shell.
For example, `~/.config/maniac/.env` can hold credentials for several providers at once:

```dotenv
GEMINI_API_KEY='your-api-key'
ANTHROPIC_API_KEY='your-api-key'
```

With no `model`/`provider` configured, maniac picks the first provider (in packaged declaration order: Gemini, Anthropic, OpenAI) whose API key is present in the environment.
Zero keys present is a hard error; two or more prints one line to stderr naming the provider chosen and how to pin it.
`MANIAC_LLM_API_KEY` overrides LiteLLM's provider-native credential lookup.
`MANIAC_MODEL` and `MANIAC_REASONING_EFFORT` are used when `config.toml` sets no `model`/`reasoning_effort`; an explicit `--model` argument and `config.toml` both outrank them.

---

## Use it

| Command | Acts on | Does |
|---|---|---|
| `install <tool>...` | the tools you name | Finds or generates each page and installs it. |
| `update [tool]...` | maniac's pages | Reinstalls each page whose tool changed version. |
| `remove <tool>...` | maniac's pages | Removes them and restores any page each replaced. |
| `list [tool]...` | maniac's pages | The version each documents, the version installed, its state. |
| `scan [tool]...` | your login `$PATH` | Your other tools and their pages, best effort. |
| `why <tool>` | one tool | Every decision maniac makes about it. |

### `install`

```text
$ maniac install npm ruff rg
npm   installed: shipped page (10.9.4)
  documents /opt/node22/bin/npm (npm, 10.9.4)

ruff   installed: generated page, from --help (14 commands) and 9 docs in astral-sh/ruff
  documents ~/.local/bin/ruff (uv, 0.6.9)
  this shell runs ~/proj/.venv/bin/ruff instead

rg   not installed: 'rg' is not on your login shell's $PATH ...

1 of 3 not installed.
```

Each tool gets one line: whether a page was installed, and which. Below it, the binary the page documents and where its version came from, and, when the shell you ran it from would run another copy, that copy.

- A page maniac already installed that `list` reads `ok` is left alone (`already up to date (10.9.4)`), so naming it again costs nothing. An `outdated` or `unknown` page is reinstalled.
- `--no-generate` uses only a shipped or upstream page. If the upstream check cannot complete (network, git), `install` says so rather than reporting that no page exists; without `--no-generate` it refuses to generate over a check that did not finish.
- `--dry-run` says what would be installed (`would install: ...`) and changes nothing.
- `--force` installs where maniac would refuse, printing a `forced:` line for each refusal it overrode:
  - a tool with no global copy, only a venv or a project's: the copy this shell runs is documented and recorded, and `list` checks that copy from then on;
  - a system package's tool, whose package normally maintains its page;
  - a page maniac did not install, sitting where it would write: kept as a backup that `remove` restores;
  - a page already current: reinstalled.

With no tools, `install` does nothing, so an empty pipe into it is harmless.

### `list` and `update`: your pages

```text
$ maniac list
                     Pages maniac installed
┏━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┓
┃ Tool   ┃ State     ┃ Documents ┃ Installed ┃ Source    ┃
┡━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━┩
│ cowsay │ unknown   │ -         │ 1.5.0     │ generated │
│ npm    │ ok        │ 10.9.4    │ 10.9.4    │ shipped   │
│ ruff   │ outdated  │ 0.6.8     │ 0.6.9     │ generated │
└────────┴───────────┴───────────┴───────────┴───────────┘
  cowsay: no version was recorded for this page
```

A page is `ok` when both versions are known and equal, `outdated` when they differ, and `unknown` when either is missing: no recorded version, a binary that cannot report one, or a pinned copy that is gone. `--outdated` and `--unknown` narrow to one state.

`update` reinstalls every `outdated` page, and nothing else:

```bash
maniac update            # every outdated page
maniac update ruff       # just this one, saying why if it is left alone
```

An `unknown` page is left alone until you name it to `maniac install`. A page `--force` pinned to a non-global copy is reinstalled for that copy. Each tool commits on its own, so an interrupted update resumes by running it again. `update` takes `--model`, `--no-generate` and `--dry-run` as `install` does.

### `scan`: your other tools

```text
$ maniac scan
                  Other tools on your $PATH
┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Tool                ┃ State     ┃ Source ┃ Upstream        ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ corepack            │ missing   │        │ nodejs/corepack │
│ cowsay (2 binaries) │ available │        │ piuccio/cowsay  │
│ hx                  │ ok        │ system │ helix-editor/…  │
└─────────────────────┴───────────┴────────┴─────────────────┘
Not shown: 2249 more on your $PATH
      2 have a page from maniac: `maniac list`
    377 no installer claims: `maniac why <tool>`
   1870 system tools, whose packages ship their pages
```

`scan` finds, best effort, every tool an installer put on your login `$PATH` that maniac has no page for, and the state of the page `man` would show for it. Besides `ok`, `outdated` and `unknown`, a tool with no page reads `available` (a shipped or upstream page can be installed) or `missing` (only a generated one is left). `--outdated`, `--unknown`, `--available` and `--missing` select states.

Source says where the page `man` shows came from: `shipped`, `upstream`, `generated`, or `system` for a page another package owns. Upstream links to the project's repository; on a terminal that supports links, Source links to the exact page.

Nothing is left out silently: `scan` ends by counting everything it passed over, by reason, on stderr. Named tools (`maniac scan hx uv`) are shown exactly, maniac's own included.

Piped, `list` and `scan` print bare tool names (`--names` does the same on a terminal), so installing every page that needs no LLM is one line:

```bash
maniac scan --available | xargs maniac install
```

### `remove`

```text
$ maniac remove npm rg
npm   removed (68 pages)
rg   maniac installed no page for it.
```

`remove` takes out a tool's pages, with every page released alongside it and everything maniac made to generate them, and puts back any page one had replaced. A page maniac did not install is never touched. Naming a companion page (`eza_colors`) while its main page is installed is refused, naming the main page to remove instead.

### `why`

```text
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

`-v` on any command prints maniac's diagnostic detail as it works.

---

## How it works

**Finding the tool.** maniac reads `$PATH` from your login shell (`$SHELL -lc`, started in `$HOME`), not from the shell you run it in, and asks each installer it knows (uv, pipx, cargo, go, npm, Homebrew, mise) whether it owns the binary found there, for the package, version, install root and repository. A mise shim is followed to the tool it runs from `$HOME`.

**Shipped and upstream pages.** A shipped page is linked, not copied, so it follows the package it lives in. Upstream pages come from the repository at the tag matching the installed version, read from cached Git tree objects without a checkout; for GitHub, release assets are inspected too, which is where some projects (eza) publish theirs. A release that ships several pages (companion pages in other sections) is installed, updated and removed as one.

**Generated pages.**

1. **Crawl**: the `--help` tree, subcommands included, with cycle protection, pager suppression and ANSI stripping. A failed `--help` counts only when its output contains a recognizable `usage:` line.
2. **Documentation**: the project's reference material, concepts and keybindings from the repository at the installed version, leaving out build scripts, CI configuration and changelogs.
3. **Write**: an LLM structures the page around the tool's domain (sessions, buffers, modes), separates global options from subcommands, and adds worked examples.
4. **Compile**: pandoc turns it into roff, installed under `~/.local/share/man/`.

Root `--help` alone is enough when nothing better exists, and repository documentation alone when the crawl fails; generation stops only when neither gives usable material. A page written from documentation for another version records no version, so it reads `unknown`.

**Bookkeeping.** Every page maniac installs is a symbolic link recorded in a manifest (`$XDG_STATE_HOME/maniac/installed.json`) with its source, version, checksum and any page it replaced. A page whose bytes changed is still removed, with a warning; a link someone repointed is no longer provably maniac's and is left alone. Repository data is cached under `$XDG_CACHE_HOME/maniac/repos`: positive results persist, definitive misses expire after five minutes, and network failures are never cached as misses.

---

## Development

Run tests, formatting and linting with [`just`](https://github.com/casey/just):

```bash
just check         # lint, format, types, unit and integration tests
just test          # the unit suite (no external tools needed)
just integration   # needs pandoc 3.1.10+, groff, man-db, npm, uvx and network
just fix           # fix lint issues and format code
```

Commands for working on maniac live under a hidden `maniac dev`; each calls a real LLM:

```bash
maniac dev eval howdoi                      # score a generated page, 0-100 by rubric
maniac dev eval howdoi --against-installed  # judge it against the page already installed
maniac dev bench                            # generate and judge across models and tools (`just bench`)
```

How maniac decides is in [`docs/CONTRACT.md`](docs/CONTRACT.md); decisions before and around it are in [`docs/adr/`](docs/adr/), and open work in [`docs/BACKLOG.md`](docs/BACKLOG.md).
