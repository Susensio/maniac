# ADR-0007: Keep all help-derived pages instead of excluding Help2man's self-manual

Status: Superseded by [ADR-0008](0008-source-backed-candidates.md)
Date: 2026-09-01
Supersedes: [ADR-0006](0006-candidate-repositories-translations.md)

## Context

ADR-0006 had excluded `help2man(1)` by name because its self-generated manual was not considered useful to improve.
That exception was product policy encoded as a command-name check, while the scanner's reliable evidence only establishes that a page is help-derived.

## Decision

`list-missing --include-candidates` will retain every page recognized by the help-derived classifier, including `help2man(1)`.
It will deduplicate localized copies by name and section, preferring the shortest installed path, without any command-specific exclusion.
It will show each candidate's discovered repository and report the post-deduplication repository lookup as a progress phase.
The global scan will perform one bounded local read per installed page before repository discovery.

## Consequences

The inventory is mechanically consistent and does not hide a recognized page based on a hardcoded name.
Candidate status remains an invitation to review rather than a claim that every page should be replaced.
