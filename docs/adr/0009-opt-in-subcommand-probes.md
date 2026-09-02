# ADR-0009: Keep source-backed candidates fast and inspect subcommands only on request

Status: Superseded by [ADR-0010](0010-default-subcommand-probes.md)
Date: 2026-09-02
Supersedes: [ADR-0008](0008-source-backed-candidates.md)

## Context

Source-backed candidates were actionable with upstream documentation, while many system help-derived pages had no trustworthy source association.
Some of those programs nevertheless exposed subcommands that Help2man did not recursively document.
Probing every command during the default global inventory would make its local filesystem scan depend on external process execution and command timeouts.

## Decision

`list-missing --include-candidates` will remain a fast source-backed inventory.
It will classify every help-derived page, deduplicate localized copies by name and section, and apply no command-specific exclusions.
Source-backed rows require a source tied to the installed executable; candidate mode will not infer a repository from a name-only registry match.
`--check-subcommands` will additionally run one short top-level `--help` probe for each source-unknown candidate and include only pages with detected subcommands.
These rows will keep their source as unknown and state the number of detected subcommands.

## Consequences

The default scan remains predictable and fast.
The opt-in mode can take longer and may miss commands whose help exceeds the short timeout, but it identifies pages MANIAC can enrich from locally crawled help.
