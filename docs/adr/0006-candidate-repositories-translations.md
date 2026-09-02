# ADR-0006: Enrich global candidates with repositories while collapsing translations

Status: Superseded by [ADR-0007](0007-no-help-derived-exceptions.md)
Date: 2026-09-01
Supersedes: [ADR-0004](0004-global-discovery-flag.md)

## Context

The first global candidate scan reported every localized copy of a help-derived page and did not show the repository MANIAC would use for improvement.
It also included `help2man(1)`, whose exact generated-file marker is correct but whose self-generated manual is not a useful candidate.
ADR-0004 had prohibited repository discovery to keep the inventory minimal, but users needed repository context to evaluate candidates.

## Decision

`list-missing --include-candidates` will deduplicate help-derived pages by name and section, preferring the shortest installed path and omitting `help2man` itself.
It will show each candidate's discovered repository and report repository lookup as a progress phase.
The scan will still perform one bounded local read per installed page; repository discovery runs only after the candidate set is reduced.

## Consequences

Candidate output is easier to review and does not repeat translations.
The global command can now use cached registry metadata and local configuration while resolving repositories, so it is no longer filesystem-only.
The explicit Help2man exclusion is intentionally narrow: other generated pages remain candidates for review rather than automatic replacement.
