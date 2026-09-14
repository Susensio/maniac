# Implementation State

Implementation in flight: use validated Mise `latest` aliases for globally selected vendor manuals, surface later alias drift as `outdated`, and preserve provider-owned target update semantics during uninstall under ADR-0029.

ADR-0028 changed every new managed manpath entry into a manifest-tracked symbolic link.
Generated, repository, and currently unqualified vendor pages materialize durably under MANIAC data storage rather than linking into the disposable cache or a versioned provider root.
Uninstall preserves replaced, retargeted, and dangling entries, and legacy checksum-matching copies migrate on manifest load.
The live migration converted the two existing managed pages, `aichat.1` and `ty.1`, to durable links.

`cli.listing.compute_rows` and GitHub release-manpage discovery are now decomposed at their actual coordination boundaries with their observed rendering, callback, ordering, deduplication, and release-validation behavior intact.
Provider registration and enumeration live in a resolution coordinator without a `discovery`/`providers` cycle.

The default unfiltered TTY view now completes provider enumeration, builds one alphabetically stable per-binary table with every Tool cell populated, then fills State, Source, and Upstream progressively without changing the table shape.
When that table is taller than the terminal, asynchronous rendering is cropped to an alternate-screen viewport and the complete final table is printed once after returning to the normal screen.
Remote-dependent states remain `checking` until their entire deduplicated probe group is finalized atomically.
Filtered, `--names`, and non-terminal output remain final-only so selection and pipelines stay correct.
The State width is derived from every state label plus `checking…`; Tool and Upstream are capped at 24 columns with ellipsis, and neither final nor streaming tables expand Upstream to fill the terminal.

Cargo metadata parsing and pipx home discovery are cached for the life of the process rather than repeated for every executable candidate.
Candidate paths are routed only to providers whose install layout can claim them, with the ordered full registry retained for ambiguous paths.
Local manpage checks run in a bounded pool from one manifest snapshot, and eligible upstream probes begin as individual missing rows become ready.
Install-root inventories are cached per root, cold Mise registry loading is single-flight, and definitive versioned upstream misses use the same five-minute negative-cache policy as missing tags.

A live profile before upstream-result caching measured 35.185 seconds for 77 rows and 35 probes; the final probes each took 2.2–8.6 seconds and caused the visible 97–99% stall.
After `1ceab84`, an empty-cache real `maniac list` completes in 22.40 seconds and a warm run in 5.60 seconds on the development system.
Before the later pipeline work, two warm TTY profiles took 21.00 and 24.47 seconds after imports; Rich publication accumulated 15.17 and 16.50 seconds because every result rebuilt the table, even when no refresh was due.
After routing, overlap, render debouncing and definitive-miss caching, a non-terminal run that populated negative entries took 18.17 seconds and its immediate warm repeat took 9.28 seconds.
On the fully integrated implementation, two warm 120-by-16 TTY runs took 4.76 and 6.04 seconds, and a warm non-terminal run took 6.09 seconds.
After the provenance and compact-column work, an isolated warm 100-by-24 TTY run took 5.08 seconds and entered/exited the alternate screen exactly once.
The remaining profile is dominated by overlapping local/provider work rather than Rich publication: Mise install-root scans, UV editable-source resolution, and npm package metadata resolution.

Skipping `/bin`, `/sbin`, `/usr/bin`, and `/usr/sbin` entirely measured the unsafe upper bound at 5.12 to 3.87 seconds for warm row computation, about 1.25 seconds or 24%, while returning the same 71 rows on this machine.
That shortcut was not retained because it loses first-PATH-entry shadowing and misses system-directory symlinks into managed installs.
A safe experiment kept the names and symlinks but skipped provider routing for 2,089 ordinary system files; isolated enumeration measured 0.551 seconds against 0.547 seconds for the old path, so the complexity produced no measurable gain and was removed.

A live 71-row inventory contains no system-package-owned installation rows, because MANIAC has no apt, pacman, or RPM provider.
It still records names from system PATH directories to preserve first-PATH-entry shadowing, but provider routing does not read those binaries or query their packages.
Package provenance runs only when a provider-managed row resolves an external page.
Debian ownership and versions are cached per process; a proven match reads `ok`, a proven mismatch reads `outdated`, and unsupported or ambiguous evidence reads `unverified`.
The live Mise tealdeer 1.9.0 binary now reads `outdated` against Debian's tealdeer 1.6.1 page, while the unresolved Python and yadm cases read `unverified`.

The legacy Mise npm layout for `bash-language-server` now resolves its explicit `package.json` repository to `bash-lsp/bash-language-server` even without `.mise.backend.toml`.

The live eza probe reports `available` / `upstream` and independently caches `eza.1`, `eza_colors.5`, and `eza_colors-explanation.5` without reading Mise `extra_assets`.

The development system still has no installed `fzf.1` in either MANIAC's data directory or mise's install root. `maniac list fzf` correctly reports `available` / `upstream` from version-matched cached Git objects without a repository worktree.

Mise correctly retains `tmux/tmux-builds` as the binary-distribution provenance for tmux.
Every documentation consumer maps that exact repository to `tmux/tmux` through packaged `defaults.toml`; no repository-name heuristic or Mise `extra_assets` field is consulted.
The mapping is used before list/install manpage probes, `source docs`, and tier-3 documentation extraction.
Help crawling now keeps successful stdout, falls back to stderr, and accepts a failed `--help` only when the combined output contains a real `usage:` line; empty or option-error-only output is a crawl failure.
Tier-3 synthesis may proceed from root help alone or repository documentation alone, warns when only root help is available, and refuses only when neither source produced usable material.
Before the LLM call it reports command and subcommand counts, the resolved repository, documentation-file count, and whether those files matched the installed version.

`list` now keeps content provenance separate from MANIAC ownership: install-root, repository, and synthesis manifest entries render `vendor`, `upstream`, and `maniac` respectively, while `--managed` selects all three.
Each Source keyword links to the exact reachable or shipped local manpage; for GitHub sources, `upstream` links to the version-pinned repository file or release asset rather than MANIAC's cache. Tier-2 installs persist that remote URI in the manifest. The Upstream column continues to link to the repository; unsupported Git hosts keep plain Source text rather than receiving a guessed URL.
Older repository-tier manifest entries recover their missing URI through the same version-pinned, cache-first probe while retaining their installed state.
Repository identity now resolves independently of local page provenance, so vendor rows such as zoxide can still show their upstream project while only missing rows pay for a remote manpage probe.
UV tools now reuse their installed distribution metadata for published packages as well as `direct_url.json` for editable installs; Serena's `Project-URL: Homepage, https://github.com/oraios/serena` therefore resolves its Upstream cell.
Process-local caching reduces repeated UV metadata scans for sibling binaries sharing one tool root from five scans to two on the development system; the isolated editable-source pass measured 0.413 seconds before and 0.132 seconds after.
Full warm TTY runs remained noisy at 6.58-7.75 seconds because transient upstream retries dominated this sandboxed measurement, so no larger end-to-end speedup is claimed.
