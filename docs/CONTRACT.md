# How maniac decides

> Adopted in [ADR-0065](adr/0065-a-user-contract-and-a-smaller-command-surface.md), live since 2026-10-10.

maniac gives the command-line tools you installed yourself a correct `man` page.
Everything a user can observe it doing follows from the four rules below.
A behaviour that cannot be traced to one of them is a bug, either in maniac or in this document.

## The rules

### 1. maniac changes only pages it installed, or tools you name

- `install` acts on the tools you name.
- `update` and `remove` act only on pages maniac installed.
- A page maniac did not install is reported and never changed.
  The one exception is a tool you name whose page sits exactly where maniac would write: `--force` replaces it, after keeping a backup that `remove` puts back.

Where `install` would refuse, `--force` installs anyway and prints each refusal it overrode (`forced: ...`).
It is the only override, and it means the same thing everywhere.

### 2. maniac documents the tool your login shell runs from your home directory

A manpage is global, so the tool it documents must be the global one.
maniac reads `$PATH` from a non-interactive login shell started in `$HOME`, which by convention runs your login profile.
An activated virtualenv, a project's mise pin or a direnv directory does not change the answer.

- Every `install` prints the binary it documents and where its version came from.
- When the shell you ran maniac from would run a different copy, maniac says so, and still documents the global one:
  `documents ~/.local/bin/ruff (uv, 0.6.9)` / `this shell runs ~/proj/.venv/bin/ruff instead`.
- A tool with no global copy (only a venv or a project pin has it) is refused, naming the copy this shell runs.
  `--force` documents that copy; the page records it, and maniac checks that copy from then on.
- Tools installed by the system package manager are left to their package, which ships and upgrades their page.
  Naming one is refused, showing the page it already has; `--force` installs one anyway, saying which page it now hides.

### 3. Every skip is counted, and every decision can be explained

- `scan` reports how many binaries it passed over, and why, in its own output.
- `why <tool>` prints the whole chain of decisions:
  - which `$PATH` it used and what it left out;
  - which binary that reaches, which installer put it there, and at what version;
  - which page `man` shows today and whose it is;
  - each source tried below, and why it was used or passed over.

### 4. When the evidence is missing, maniac says "unknown"

A page's version is known only from evidence:
the installer's own record, an upstream tag matching the installed version, or the binary's own `--version`.
Without evidence a page reads `unknown`; it never reads `ok` by default.

## Where a page comes from

maniac takes the first of these that exists:

| Source | What it is |
|---|---|
| `shipped` | The page the tool itself installed, linked where `man` finds it. |
| `upstream` | The upstream project's page for exactly the installed version. |
| `generated` | Written by an LLM from the tool's own `--help` and its repository's documentation, for the installed version. |

Only `generated` costs a model call, and `--no-generate` forbids it.

## States

| State | Meaning |
|---|---|
| `ok` | A page is reachable and evidence says it documents the installed version. |
| `outdated` | Evidence says the page documents another version. |
| `unknown` | A page is reachable but nothing proves which version it documents. |
| `available` | No page is reachable, but a `shipped` or `upstream` one can be installed. |
| `missing` | No page is reachable and none exists to install; only `generated` is left. |

## Commands

| Command | Acts on | Does |
|---|---|---|
| `install <tool>...` | the tools you name | Finds or generates each page and installs it, printing what it documents. |
| `update` | maniac's pages | Reinstalls each page whose tool changed version. |
| `remove <tool>...` | maniac's pages | Removes the page and restores any page it replaced. |
| `list` | maniac's pages | Each page: the version it documents, the version installed, its state. |
| `scan` | your `$PATH` | Your other tools and the state of their pages, best effort, with every skip counted. |
| `why <tool>` | one tool | The full decision trace (rule 3). |

Piped, `list` and `scan` print bare tool names, so `maniac scan --available --missing | xargs maniac install` installs every page that is missing.

Developer commands (quality evaluation, benchmarks) live under `maniac dev` and are left out of the main help.

## How the rules settle questions

Edge cases are decided by the rules, not by new decisions:

- *Should `update` touch pages maniac did not install?* No (rule 1).
- *Which ruff does a page document inside an activated venv?* The global one, and maniac says so (rule 2).
- *A tool only a project has?* Refused, naming the project's copy; `--force` documents it (rules 1 and 2).
- *A mise shim?* The binary mise runs for it from `$HOME` (rule 2).
- *A page installed before maniac recorded versions?* `unknown`; `update` leaves it alone until you name it (rules 1 and 4).
- *A wrapper script no installer claims?* Its page is generated from its own help, and its version is its own `--version` (rule 4).
- *A setup that builds `$PATH` only in an interactive `.bashrc`?* Those tools are not on the login `$PATH`; `why` shows exactly that (rules 2 and 3).

## Changing this contract

An ADR is written only when a change alters what this document says.
Anything else is decided by the rules and explained in its commit message.
