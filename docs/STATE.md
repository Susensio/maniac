# State

Work committed to and not yet finished. Delete this file when it is done.

## Shell completions (ADR-0066), on branch `completions`

Started 2026-10-13. Merges to `master` by the gated move when every step is done and `just check` passes.

Steps:
- [x] Find a tool's completions: shipped files in the install root; the tool's own generator, detected from its crawled `--help` and validated per shell (`sources/completions.py`)
- [x] Record them: manifest `completions` section, schema version 2, read by version-1 manifests as empty
- [ ] Install them: links in each shell's user directory; generated output saved under maniac's data directory; a file maniac did not install is refused
- [ ] Commands: `install` (default on; `--shell`, `--no-completions`), `update`, `remove`, `list` (a column), `why` (a section); zsh `$fpath` check that prints the line to add
- [ ] Docs (README), snapshots reviewed, `just check`, merge
