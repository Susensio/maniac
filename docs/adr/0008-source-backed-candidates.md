# ADR-0008: Show only source-backed candidates instead of every help-derived page

Status: Superseded by [ADR-0009](0009-opt-in-subcommand-probes.md)
Date: 2026-09-02
Supersedes: [ADR-0007](0007-no-help-derived-exceptions.md)

## Context

The global scan classified every help-derived page, but a name-only registry lookup
could associate ordinary system commands with unrelated repositories. A candidate
without an installed source association could only be regenerated from local help,
not improved from authoritative upstream documentation.

## Decision

`list-missing --include-candidates` will scan every help-derived page without
command-specific exclusions, then list only pages with a source tied to the
installed executable. It will skip pages whose source is unknown and report their
count. Candidate mode will not use name-only registry matches for system binaries.

## Consequences

The candidate table is smaller, but every listed page has an actionable source.
General repository discovery retains official-registry lookup for explicit
`docs` and `generate` requests.
