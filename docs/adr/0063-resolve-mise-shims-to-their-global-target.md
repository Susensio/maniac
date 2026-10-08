# ADR-0063: Resolve a Mise shim to the binary it runs from $HOME before any provider looks at it

Status: Accepted
Date: 2026-10-08

## Context

ADR-0062 assumes a conventional setup, and for Mise that means `mise activate --shims` in the login profile.
The login `$PATH` then starts with `<mise data>/shims/`, and every shimmed tool's first hit is a shim.

Measured with mise 2026.10.4: a shim is a symlink to the `mise` executable.
When run, mise picks the tool version for the current directory.
With `npm:cowsay@1.5.0` selected globally and `1.6.0` pinned in a project, `mise which cowsay` answered 1.5.0 from `$HOME` and 1.6.0 inside the project.
`mise bin-paths` from `$HOME` listed the global install's bin directory.

No provider claimed a shim.
Its resolved path is the `mise` binary, outside `mise/installs/`, so a shimmed tool fell to tier-3 synthesis from `--help`.
Worse, that `--help` ran the shim from maniac's working directory, so inside a project it described the project's version.
Because every shim resolves to the same `mise` file, `list` (ADR-0049) would also have grouped all shimmed tools as one binary.

## Decision

A `$PATH` entry is a Mise shim when it sits in a directory named `shims` and resolves to a file named `mise`.
Before routing to providers, `resolution._detect_via_registry` replaces a shim with its target: the first directory from `mise bin-paths`, asked once per process from `$HOME` through `discovery._run_mise`, that holds an executable of the same name.
The target is then detected as any `$PATH` hit would be, so the Mise provider supplies version, root and ADR-0061's global-selection check unchanged.

`Installation.bin_path` is the file that runs: the target for a shim, the `$PATH` hit otherwise.
`resolve_tool` runs `installation.bin_path` when a provider claims the binary, so `--help` and `--version` run the global install directly, never the shim.

A shim no globally active tool provides raises `NotGloballySelected`, naming the shim.
Mise writes shims for every installed version, project-only ones included.
`mise` failing behind a shim raises `MalformedToolMetadata`, as other Mise lookups do: a shim already found is evidence that mise should work here.

## Consequences

A shimmed tool is claimed as its global Mise install and reaches tiers 1 and 2, regardless of the directory maniac runs in.
Verified live: from inside the project pinning 1.6.0, maniac claimed and crawled 1.5.0.

One extra `mise` call per process (`bin-paths`), cached with the existing single-flight cache, failures included.

`maniac list` shows a row for each shim of a project-only tool, refused as not globally selected, the same visible row ADR-0061 gives project-only installs.
With shims these rows appear even outside the project.
(Superseded the same day; see Corrections.)

A `mise shell` session pin survives the `$HOME` query (ADR-0061 Corrections), so `bin-paths` can point at the session's version.
The install's own global-selection check drops environment-sourced entries and refuses it.

When two global tools ship the same binary name, the target is the first `bin-paths` directory holding it.
This is assumed to match mise's own dispatch order and has not been measured.

## Corrections

2026-10-08: a shim no global tool provides was refused outright, and the user found the resulting `list` rows (one per project-only tool, everywhere) noise.
Measured against real mise 2026.10.4 from `$HOME`: such a shim falls through to the next binary of that name on `$PATH`, outside the shims directory; with none, it errors.
maniac now follows the same rule.
The fallthrough target is detected like any other binary, claimed or not.
Only a shim with nothing to fall through to raises, as `ShimRunsNothing` (a `NotGloballySelected`).
`list` with no names leaves it out, since the name reaches no binary from `$HOME`; asked for by name, `install` and `list` still give the reason.

This follows mise's own dispatch, not ADR-0020's rejected positional fall-through: the shim is not an unclaimed binary that maniac skips past, but a dispatcher whose documented behaviour is to run that later binary.

Every lookup that turns a name into a runnable path now goes through `resolution.binary_path`, which applies the shim swap.
Before, an unclaimed fallthrough target left `resolve_tool` running the shim itself, and `list` probed an unclaimed binary's `--version` through it.
From inside a project, both answered with the project's version.
