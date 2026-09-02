# ADR-0005: Read optional Mise config and official registry instead of the Mise executable

Status: Accepted
Date: 2026-09-01

## Context

Repository discovery had read local Mise configuration and then invoked `mise registry` for public mappings.
That made a separate tool installation necessary for a feature that only needed registry metadata.
The official Mise registry provides the same public entries, including aliases and binary names, as a zstd-compressed TOML archive.
Local configuration could still contain intentional user-specific mappings that no public registry knows.

## Decision

MANIAC will resolve a repository from local installation metadata first, optional local Mise configuration second, and a cached official Mise registry third.
The registry cache will refresh at most once per hour and fall back to the tool name if it is unavailable.
It will read GitHub- and Aqua-backed entries, aliases, and binary names in-process with `zstandard`.
MANIAC will not invoke or require the Mise executable.

## Consequences

Users retain local aliases and private mappings without installing Mise.
The first public-registry lookup needs network access, while later lookups use the cache.
Registry availability or format changes can reduce public mapping quality, but discovery still falls back without blocking generation.
The project now maintains a direct decompression dependency instead of a runtime tool dependency.
