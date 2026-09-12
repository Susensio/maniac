# Implementation State

No implementation is currently in flight.

The default unfiltered TTY view now completes provider enumeration, builds one alphabetically stable per-binary table with every Tool cell populated, then fills State, Source, and Upstream progressively without changing the table shape.
When that table is taller than the terminal, asynchronous rendering is cropped to an alternate-screen viewport and the complete final table is printed once after returning to the normal screen.
Remote-dependent states remain `checking` until their entire deduplicated probe group is finalized atomically.
Filtered, `--names`, and non-terminal output remain final-only so selection and pipelines stay correct.

Cargo metadata parsing and pipx home discovery are cached for the life of the process rather than repeated for every executable candidate.
Candidate paths are routed only to providers whose install layout can claim them, with the ordered full registry retained for ambiguous paths.
Local manpage checks run in a bounded pool from one manifest snapshot, and eligible upstream probes begin as individual missing rows become ready.
Install-root inventories are cached per root, cold Mise registry loading is single-flight, and definitive versioned upstream misses use the same five-minute negative-cache policy as missing tags.

A live profile before upstream-result caching measured 35.185 seconds for 77 rows and 35 probes; the final probes each took 2.2–8.6 seconds and caused the visible 97–99% stall.
After `1ceab84`, an empty-cache real `maniac list` completes in 22.40 seconds and a warm run in 5.60 seconds on the development system.
Before the later pipeline work, two warm TTY profiles took 21.00 and 24.47 seconds after imports; Rich publication accumulated 15.17 and 16.50 seconds because every result rebuilt the table, even when no refresh was due.
After routing, overlap, render debouncing and definitive-miss caching, a non-terminal run that populated negative entries took 18.17 seconds and its immediate warm repeat took 9.28 seconds.
On the fully integrated implementation, two warm 120-by-16 TTY runs took 4.76 and 6.04 seconds, and a warm non-terminal run took 6.09 seconds.
The remaining profile is dominated by overlapping local/provider work rather than Rich publication: Mise install-root scans, UV editable-source resolution, and npm package metadata resolution.

A live 71-row inventory currently contains four `system` page sources (`pydoc3`, `python3-config`, `tldr`, and `yadm`) but no system-package-owned installation rows, because MANIAC has no apt, pacman, or RPM provider.
`system` describes where `man` resolved a page, not who owns the binary; for example, the mise Python 3.14 commands currently resolve Debian Python 3.12 pages.
The same rule currently reports mise tealdeer 1.9.0 as `ok` from Debian's tealdeer 1.6.1 page; the required provenance/state decision is recorded in `docs/BACKLOG.md` rather than hidden as a completed result.

The legacy Mise npm layout for `bash-language-server` now resolves its explicit `package.json` repository to `bash-lsp/bash-language-server` even without `.mise.backend.toml`.

The live eza probe reports `available` / `upstream` and independently caches `eza.1`, `eza_colors.5`, and `eza_colors-explanation.5` without reading Mise `extra_assets`.

The development system still has no installed `fzf.1` in either MANIAC's data directory or mise's install root. `maniac list fzf` correctly reports `available` / `upstream` from version-matched cached Git objects without a repository worktree.

`tmux` also has no installed manpage. Its current `tmux/tmux-builds` mis-resolution, empty-stdout help handling, and insufficient synthesis-input guard are recorded as separate open defects in `docs/BACKLOG.md`.
