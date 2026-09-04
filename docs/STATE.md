# Candidate Discovery Handoff

## Current behaviour

`maniac list-missing` examines executable files in the selected binary directory and lists pages that are missing.

`maniac list-missing --include-candidates` additionally performs a global search of the active manpath.

The global scan invokes `manpath` once, walks only its returned local directories, reads at most 8 KiB from each plain or supported compressed manpage, and identifies Help2man pages from the exact generated-file marker.

Localized copies are deduplicated by `(name, section)`, retaining the shortest path.

There is no command-name exclusion, including for `help2man` itself.

Candidate rows are actionable through one of two independent mechanisms.

- A source-backed row has an installed source tied to its executable, such as a local project, UV tool metadata, or a Mise installation.
- A subcommand-backed row has no proven source but exposes top-level subcommands through local help, so MANIAC can add recursively crawled help that Help2man did not generate.

Source-unknown subcommand rows show `Unknown` in the source column.

Known remote sources render as OSC 8 terminal hyperlinks without an underline.

## Candidate subcommand probes

`--include-candidates` probes every source-unknown candidate that is available on `$PATH`.

The probe runs the resolved absolute executable path with `--help`, has a one-second timeout, and extracts only top-level subcommands.

Candidates without subcommands are omitted unless they are source-backed.

Candidates whose manpage exists but whose executable is unavailable are skipped before process creation.

This avoids noisy warnings such as `config.guess --help` when the page is installed but the command is not on `$PATH`.

On the development system, `config.guess` and `config.sub` have executable files under `/usr/share/misc/`, while `pysetup3.12` has a stale manpage but no executable.

## Repository resolution

General `docs` and `generate` discovery may use the official cached Mise registry after local installation metadata and optional Mise configuration.

The next resolver must inspect metadata from the resolved installation before falling back to the Mise registry.
For example, the Mise npm installation of `bash-language-server` is declared in `~/.config/mise/conf.d/lsp.toml`, and its installed `package.json` names `https://github.com/bash-lsp/bash-language-server` as its repository.
Mise configuration discovery already includes `conf.d/*.toml`; the current gap is that the TOML parser only recognizes GitHub and Cargo keys, then loses npm provenance when the registry has no matching entry.

Treat the resolved package root and manifest as installation-tied evidence.
Parse npm `package.json` first (`repository`, `homepage`, `bin`, and version), then add corresponding Python, Cargo, Go, Homebrew, and native-package-manager metadata as separate resolvers.
Use a registry API only with an identified package and backend, never as a bare-command fallback.

Candidate discovery is deliberately stricter.

It requires an installed executable and installation-derived evidence before accepting a source.

It never turns a bare command name into a repository through the registry.

This prevents collisions observed in the previous implementation, including `fmt` resolving to `nushell/nufmt`, `od` resolving to `todotxt/todo.txt-cli`, and GNU `envsubst` resolving to `a8m/envsubst`.

Mise configuration matching is exact for repository tails and `filter_bins`; it no longer uses substring matches.

Candidate-mode Mise resolution disables the binary-name fallback even for a managed installation.

The cached official registry matched `mise registry --json` for all 778 shared GitHub/Aqua short names tested on the development system, with zero conflicting repository selections.

The parser also knows executable aliases and bins, which is useful for explicit discovery but not equivalent to the Mise CLI and is therefore not trusted for candidate attribution.

## Stronger package-based source proposal

The next safe resolver should use native package provenance before any registry-name inference.

On Debian-family systems, resolve the exact manpage path with `dpkg-query -S -- PATH`.

Then inspect the owning package, its source package, package homepage and VCS fields where present, package copyright, and its owned files.

This ties the evidence to the installed file and can find commands that are package-owned but not on `$PATH`, such as `/usr/share/misc/config.guess`.

Package VCS metadata can name a downstream distribution repository rather than upstream.

The UI and source model must preserve that distinction as a package source rather than presenting it as an upstream repository.

Package provenance should be cached or queried in batches because a global manpath scan can contain hundreds of candidate pages.

## Additional documentation sources

Repository documentation extraction currently reads selected text formats from a source tree.

It must also extract the free documentation already present in a resolved local package root, without requiring a clone or network access.
Read every eligible documentation file at that root and under known documentation directories, but continue excluding dependency, build, generated, and test trees such as `node_modules`.
The `bash-language-server` package root already contains a top-level `README.md`, which is authoritative package-local context currently left unread.

Preserve provenance when combining context: distinguish upstream-repository documents, installed-package documents, package-manager documents, Info pages, and local help.

The package-provider implementation should add bounded local sources for system-installed candidates.

- `/usr/share/doc/<package>/README*`, `NEWS`, and examples.
- `/usr/share/info/<tool>.info*` and relevant localized Info pages.
- The package description as supplementary context only.
- The package-owned executable's actual `--help` output, using its absolute path.

These sources should be kept separate from repository docs so the prompt can state their provenance.

## Decisions

ADR-0005 records the removal of the Mise executable dependency in favour of optional local configuration and the official registry archive.

ADR-0010 is the current candidate policy and supersedes ADR-0009.

It requires generic Help2man classification and deduplication, installation-tied sources, no name-only repository attribution, default one-second subcommand probes for available unbacked commands, and `Unknown` source presentation for subcommand-backed rows.

## Verification performed

The current implementation passed `just check`: Ruff formatting, Ruff linting, ty type checking, and 136 pytest tests.

An independent audit passed the default probe, PATH guard, source-attribution, and ADR-0010 criteria.

The development system confirmed that `config.guess`, `config.sub`, and `pysetup3.12` are not on `$PATH` and are skipped without invoking them.

## Working-tree note

The repository contains uncommitted, related work across candidate discovery, Mise registry discovery, configuration, LLM integration, documentation, and tests.

Do not reset or discard unrelated changes when continuing this work.
