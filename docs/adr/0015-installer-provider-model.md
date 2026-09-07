# ADR-0015: Resolve tools through installer providers instead of symlink-prefix matching

Status: Accepted
Date: 2026-09-07

## Context

`sources/discovery.py` resolved a tool's upstream by resolving its PATH symlink and matching the target against three hardcoded substrings: `/.local/lib/`, `/.local/share/uv/tools/`, and `/.local/share/mise/installs/`.
Everything else returned nothing.

Three problems had accumulated behind that shape.

The rule was fitted to one machine.
On the development system every entry in `~/.local/bin` was a symlink and no other installer was present, so the three prefixes were exactly right and nothing exercised the gap.
Installers whose binaries are real files rather than symlinks — cargo, go — could not be detected at all, because `discover_candidate_source` returned early on `not bin_path.is_symlink()`.
Installers with the identical symlink shape but a different prefix — Homebrew's `bin/foo -> ../Cellar/foo/1.2.3/bin/foo`, pipx's venvs — were excluded by omission rather than by decision.

Mise is a meta-installer, and the code reverse-engineered that fact rather than reading it.
`_resolve_from_mise` special-cased tool-id prefixes (`github-`, `pipx-`, `npm-`, `cargo-https-github-com-`) to recover which backend had performed the install.
Mise records this outright: `~/.local/share/mise/installs/<tool>/.mise.backend.toml` holds `full = "aqua:biomejs/biome"` or `full = "github:ogulcancelik/herdr"`.
19 of roughly 50 installs on the development system carried that file; the prefix parsing was inferring what the remaining path spelled directly.

The resolution result was a bare `RepoSource`, which discards everything except a repository target.
The installed version was resolved during discovery and then dropped, so the three separate pieces of work that need it — pinning fetched documentation to the installed version, recording generation inputs so a page can be called stale, and reading a package manifest at the install root — each had to re-derive it.

Measurement ruled out one motivation that had been assumed.
Enumerating installations rather than manpages was expected to be much cheaper than the existing manpath scan.
It is not: resolving all 588 PATH symlinks took 1.82s against 1.96s for classifying 5504 manpages, with a fixed 0.84s of interpreter and import startup underneath both.
The case for the change is capability, not speed.

Separately, ADR-0008 established that a source must be proven by the installation rather than guessed from a name, after name-only registry lookups produced real mis-attributions (`fmt` to `nushell/nufmt`, `od` to `todotxt/todo.txt-cli`, GNU `envsubst` to `a8m/envsubst`).
It applied that rule to enumeration only and deliberately left explicit requests permissive.
The asymmetry was live and reachable: `discover_repo("envsubst")` still returned `a8m/envsubst` on the development system, so `generate` would synthesize a page for GNU `envsubst` describing an unrelated Go program, and install it over the vendor page.

## Decision

Tool resolution goes through a provider per installer, selected by detection rather than by a prefix table.

A provider answers three separate questions, as three methods:
detection, which is pure-filesystem and cheap;
source resolution, which may consult a registry;
and local documentation, which is pure-filesystem.
No provider reaches into another's internals.

Detection yields an `Installation` value carrying the binary name, the path that was resolved, the provider that claimed it, the package identity in that provider's namespace, the installed version, and the install root.
`Installation` becomes the type every other layer consumes.
`RepoSource` remains what source resolution returns, but it is no longer the interface between discovery and the rest of the system.

Mise composes rather than special-cases: it detects the installation, reads `.mise.backend.toml` for the backend and package identity, and delegates source resolution to the provider named there, linked through `Installation.parent`.
Where that file is absent, the mise registry is consulted using the install directory name.
That name comes from the installation and is therefore evidence under ADR-0008's rule, unlike a bare binary name.

`is_symlink()` stops being a precondition of detection.
Each provider decides what evidence identifies its own installations.

The ADR-0008 rule extends to every caller, not only enumeration.
Name-only registry matching is removed rather than left available to explicit requests: a tool with no provider is reported as unresolvable and nothing is generated for it.

Providers to implement: mise, uv tools, `~/.local/lib` checkouts, npm global, pipx, cargo, go, and Homebrew.
Cargo reads `$CARGO_HOME` rather than assuming `~/.cargo`; on the development system `CARGO_HOME` is `~/.local/share/cargo`.

## Consequences

Coverage becomes additive: a new installer is a new provider, not another branch in a shared function.

The `Installation` type unblocks three pieces of work that were each blocked on the same missing value, and makes the installed version available at the point of use rather than re-derived three times.

Detection is no longer gated on a symlink, so cargo and go become reachable — but neither is installed on the development system, and mise's own cargo backend does not exercise a standalone cargo provider, since those installs live in mise's tree.
Verifying cargo and go requires creating real installations first.
Homebrew and pipx are likewise unverifiable on the development system.
Providers written against documented layouts but never run against a real installation are the known weak point of this decision.

Removing name-only registry matching costs coverage deliberately.
Tools resolvable only by guessing from their name stop being generatable, and that is the intent: a confidently wrong manpage installed over a correct vendor page is worse than no manpage.

Resolution stays vulnerable to a distinct problem this decision does not solve.
A registry, and `.mise.backend.toml` equally, answers where a binary is downloaded from, which is not always where its documentation lives — `discover_repo("tmux")` resolves to `tmux/tmux-builds`, a prebuilt-binary mirror, rather than `tmux/tmux`.
Provider composition does not fix that, and it remains open in `docs/BACKLOG.md`.

Nothing here makes `status` faster.
The measurement above is recorded so the speed argument is not made again from intuition.
