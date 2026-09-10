# Implementation State

No implementation is currently in flight. Open work remains in `docs/BACKLOG.md`.

The development system still has no installed `fzf.1` in either MANIAC's data directory or mise's install root. `maniac list fzf` now correctly reports `available` / `upstream` from the version-matched cached checkout at `~/.cache/maniac/repos/fzf@v0.74.3`.

`tmux` also has no installed manpage. Its current `tmux/tmux-builds` mis-resolution, empty-stdout help handling, and insufficient synthesis-input guard are recorded as separate open defects in `docs/BACKLOG.md`.
