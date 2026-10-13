"""maniac: manpages for the tools you install, kept matching their versions.

Layers, lowest first; each imports only those below it, and siblings
(on one line) not each other -- pyproject.toml's `[tool.importlinter]`
enforces it, and every top-level module must be placed:

- `models`, `logging`, `templates`; then `exceptions`; then `config`
- `manifest` (maniac's record of what it installed), `github_token`
- `sources` finds binaries, installers and pages; `lifecycle` keeps
  maniac's manpath entries
- `state` judges a page; `installer` writes one; `generation` synthesizes one
- `orchestration` (`install`, `why`), `listing` (`scan`'s discovery),
  `evaluation` (judging generated pages)
- `bench`; then `cli`, which draws them; then `__main__`

Each package's own docstring says what it holds and which entry points to
reuse.
"""

__version__ = "0.1.0"
