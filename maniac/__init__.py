"""maniac: manpages for the tools you install, kept matching their versions.

Packages, lowest layer first (`audit.toml`'s layering rule enforces the
order): `sources` finds binaries, installers and pages; `state` judges a
page; `listing` (`scan`'s discovery) and `orchestration` (`install`, `why`)
build on both; `cli` draws them. Each package's own docstring says what it
holds and which entry points to reuse.
"""

__version__ = "0.1.0"
