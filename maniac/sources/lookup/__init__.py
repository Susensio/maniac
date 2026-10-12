"""The two questions every command asks, each answered in one place.

`resolution`: which binary the login shell runs for a tool, who installed it,
at what version (`locate`, `locate_file`, `locate_all`, `this_shell_runs`).
`candidates`: where its page comes from, in order -- shipped, then upstream --
and which repository documents it (`attempts`, `repository_of`).

Both sit above the rest of `sources` (path resolution, installers' own
records, repository fetching), which imports nothing from here.
"""
