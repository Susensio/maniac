# ADR-0057: Take documentation from local pages and upstream sources, never from third-party manpage aggregators or online mirrors

Status: Accepted
Date: 2026-09-24

## Context

MANIAC gathers documentation for a tool from the installation itself -- local manpages and Info -- and from the tool's own upstream repository and releases.
Several third-party corpora were considered as further sources: linuxcommandlibrary.com, man7.org, the Debian and Ubuntu manpage mirrors, and the GNU online manuals.
The decision was taken before 2026-09-24 and lived until then as a "Settled exclusions" entry in `docs/BACKLOG.md`, a file for open work; it moved here because it binds future source adapters and has no line of code to sit beside.

## Decision

Do not use linuxcommandlibrary.com at all.
Its corpus is generated, lossy and weakly attributed, so nothing taken from it can carry the provenance MANIAC records for every source.

Do not fetch man7.org, the Debian/Ubuntu manpage mirrors or the GNU online manuals where local pages and Info already supply the same content.
They are copies of what a local package installs, so fetching them adds network cost and a version mismatch against the installed build, and no new content.
Where a local section is missing, install the package that ships it (`manpages-dev`, for instance) instead.

## Consequences

Every documentation source stays one whose provenance ties back to the installation or its upstream, so the provenance rule needs no exception.
A tool whose only good documentation lives on one of these sites gets synthesis or nothing, and a machine missing a local manpage package gets a weaker result until that package is installed.
A new source adapter has to show it is neither an aggregator nor a copy of locally installable content.
