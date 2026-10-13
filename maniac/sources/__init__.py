"""Everything maniac reads about a tool, before judging or installing anything.

Reuse the two seams in `lookup` rather than their parts: `lookup.resolution`
answers which binary runs, who installed it and at what version;
`lookup.candidates` answers where its page comes from, in order, and which
repository documents it. Below them: `pathcache` (the login `$PATH`),
`providers` (one module per installer), `manpages` (pages on disk), `docs`
(repositories and release assets), `crawler` (`--help`), `packages` and
`roff` (verifying a page another package owns), `discovery` (mise's
registry and config), `completions` (a tool's shell completions: shipped,
or its own generator).
"""
