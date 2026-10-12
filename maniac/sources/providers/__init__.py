"""One module per installer (uv, pipx, cargo, go, npm, Homebrew, mise, local checkouts).

Each says whether it installed a binary and, if so, the package, version,
install root and repository its own records name. Commands never call a
provider directly: `sources.lookup.resolution` asks them, in `registry` order.
"""
