# Implementation State

No implementation is currently in flight. Open work remains in `docs/BACKLOG.md`.

A live profile before upstream-result caching measured 35.185 seconds for 77 rows and 35 probes; the final probes each took 2.2–8.6 seconds and caused the visible 97–99% stall.
After `1ceab84`, an empty-cache real `maniac list` completes in 22.40 seconds and a warm run in 5.60 seconds on the development system.

The live eza probe reports `available` / `upstream` and independently caches `eza.1`, `eza_colors.5`, and `eza_colors-explanation.5` without reading Mise `extra_assets`.

The development system still has no installed `fzf.1` in either MANIAC's data directory or mise's install root. `maniac list fzf` correctly reports `available` / `upstream` from version-matched cached Git objects without a repository worktree.

`tmux` also has no installed manpage. Its current `tmux/tmux-builds` mis-resolution, empty-stdout help handling, and insufficient synthesis-input guard are recorded as separate open defects in `docs/BACKLOG.md`.
