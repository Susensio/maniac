# ADR-0026: Treat unresolved external pages as unverified and prove staleness beyond MANIAC-owned pages

Status: Accepted
Date: 2026-09-12
Supersedes: [ADR-0018](0018-list-reports-manpage-reachability.md)

## Context

ADR-0018 defined `ok` as any page reachable through `man` unless MANIAC's
manifest positively proved its own page stale.  That made reachability visible,
but deliberately left every externally installed page outside freshness checks.

The development system exposed a case where the missing evidence was obtainable
locally.  The `tldr` command resolved to a Mise installation of tealdeer 1.9.0,
while `man -w tldr` resolved a page owned by Debian's tealdeer 1.6.1 package.
The row consequently read `ok / system` even though the package identity matched
and the versions positively disagreed.  Treating every system path as stale
would have been another unsupported inference: distributions may split, alias,
omit, or independently package manuals, and a path under `/usr/share/man` alone
proved neither ownership nor version.

Native package managers exposed the needed provenance without scanning the
machine's complete system executable inventory.  The lookup could be restricted
to a provider-managed row after `man` resolved a page outside its installation
root.  Debian supplied page ownership and package version through `dpkg-query`;
Arch and RPM-family systems had analogous ownership interfaces, but were not
available on the development system for verification.

## Decision

`list` retains ADR-0018's name, inventory boundary, columns, pipe composition,
filters, and version-matched upstream behavior, but replaces its four-state
freshness rule with five states:

1. `ok` — a page resolves and its provenance makes it current, or no positive
   evidence suggests it is stale where no supported verifier exists;
2. `unverified` — an external page resolves, but a supported verifier cannot
   prove that its package identity and version match the active installation;
3. `outdated` — a page resolves and positive version evidence proves that it
   documents a different version, regardless of whether MANIAC installed it;
4. `available` — no page resolves, and a version-matched page is available
   without synthesis;
5. `missing` — no page resolves and no free page is known.

MANIAC-owned pages keep using their manifest entry as evidence.  Pages inside
the detected installation root remain current by construction.  Other pages are
verified through a native package-ownership adapter only after they resolve for
a provider-managed binary.  A verifier must prove that the page-owning package
and the detected installation describe the same package before comparing their
versions.  A proven match yields `ok`, a proven mismatch yields `outdated`, and
incomplete or ambiguous evidence yields `unverified`.  Directory location,
repository-name suffixes, and binary-name similarity are not evidence.

The first adapter is Debian's `dpkg-query`, exercised on the development system.
Unsupported package managers produce `unverified`; adapters for them are added
only with real installations to test.  Ownership and version results are cached
per process, and no package-manager query runs for the thousands of system PATH
entries that no installer provider claims.

`--unverified` joins the state filters.  Like the existing state filters it
selects rows explicitly for terminal and piped output; unfiltered output remains
unchanged.

## Consequences

The concrete tealdeer mismatch no longer receives an `ok` label, and the same
rule can extend across distributions without assuming that every system binary
ships a manual.  Unknown provenance becomes visible instead of being silently
equated with freshness.

The action ladder grows by one public value and one filter.  Scripts that parse
the human table may see `unverified`; scripts consuming unfiltered names still
receive the same rows.  An unverified page is usable by `man`, so the state does
not by itself say that replacing it is safe or necessary.

External verification adds subprocess work only for the small set of detected
installations whose page resolves outside their root.  Cached, targeted lookups
keep that cost off provider enumeration and the initial table.  Results on a
system without a supported native adapter are more cautious than ADR-0018: the
page reads `unverified` rather than `ok`.

The distribution adapters are intentionally asymmetric until verified against
real systems.  Debian gains exact package-backed freshness first; Arch, RPM,
Homebrew, and other package systems remain visible omissions rather than
untested path-shape guesses.
