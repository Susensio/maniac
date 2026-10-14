"""The questions every command asks, each answered in one place.

`resolution`: which binary the login shell runs for a tool, who installed it,
at what version (`locate`, `locate_file`, `locate_all`, `this_shell_runs`) --
and, when the login `$PATH` misses a global install the invoking shell
reaches, that it did (`Outcome.OFF_LOGIN_PATH`).
`candidates`: where its page comes from, in order -- shipped, then upstream --
and which repository documents it (`attempts`, `repository_of`); and which
page people wrote for another version a generated page starts from
(`seed_for`).
`completions`: where each shell's completion comes from, in order -- shipped,
upstream, the tool's own generator, derived (`completion_for`, given a
`Subject`) -- and which shell is the login one (`login_shell`).

All three sit above the rest of `sources` (path resolution, installers' own
records, repository fetching), which imports nothing from here.
"""
