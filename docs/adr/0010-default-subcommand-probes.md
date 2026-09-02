# ADR-0010: Probe subcommands by default instead of source-only candidate filtering

Status: Accepted
Date: 2026-09-02
Supersedes: [ADR-0009](0009-opt-in-subcommand-probes.md)

## Context

ADR-0009 kept the global candidate inventory filesystem-fast by making subcommand probing opt-in.
That made `--include-candidates` omit source-unknown pages even when MANIAC could improve them by recursively crawling local help.
The probe was bounded to one second per available command, while unavailable commands could be skipped before process creation.

## Decision

`list-missing --include-candidates` will classify every help-derived page, deduplicate localized copies by name and section, and apply no command-specific exclusions.
It will include source-backed pages only when their source is tied to the installed executable, never from a name-only registry match.
It will also probe every source-unknown command available on `$PATH` once with a one-second top-level help timeout and include only pages with detected subcommands.
Pages unavailable on `$PATH` will be skipped without executing them or logging a failed command.
Subcommand-backed rows will retain `Unknown` as their source.

## Consequences

The candidate scan can take longer than a filesystem-only walk, but every listed candidate has either an installed source or additional local help MANIAC can crawl.
Commands installed only as manpages do not create noisy warnings.
