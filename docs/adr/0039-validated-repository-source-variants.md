# ADR-0039: Represent repository sources as validated variants instead of correlated fields

Status: Accepted
Date: 2026-09-14

## Context

`RepoSource` encoded four incompatible kinds of repository identity in one `target`
string: a GitHub `owner/repo`, a `LOCAL:/path`, an HTTP clone URL, or an installer
backend identifier.
The string was correlated with an `is_local` boolean and an optional `local_path`, but the
type did not prevent combinations that could not describe a real source.
Callers then re-parsed `target` to recover facts the provider had already known, including
whether a remote identity justified a clone URL.

That parsing carried one deliberate asymmetry.
An `aqua:` backend identity embeds a GitHub `owner/repo` and can justify a clone URL, while
other backend identifiers do not prove GitHub ownership and must not be guessed into one.
ADR-0015 requires repository identity to come from installation evidence, and ADR-0027
separates that identity from the provenance of a selected manpage.

## Decision

Repository sources are represented by two frozen, slotted variants.
`LocalRepoSource` carries a name and path; its canonical identity is derived from that path
and it has no clone URL.
`RemoteRepoSource` carries a name, canonical identity and clone URL established when the
variant is created.
Production callers construct the appropriate variant and consume its identity, path or
clone data without interpreting a second field.

Remote normalization happens once at `RemoteRepoSource.from_identifier()`.
HTTP(S) identities retain themselves as clone URLs, bare `owner/repo` identities map to
GitHub clone URLs, `aqua:` retains its established two-segment GitHub mapping, and every
other backend-qualified identity retains no clone URL.

`RepoSource` remains as the common type and as a compatibility constructor for the retired
field bundle.
That constructor validates the old spelling and returns one of the two variants, rejecting
contradictory local/remote combinations rather than allowing them into production.
Legacy `target`, `is_local` and `local_path` attributes remain derived read-only properties
on the variants while callers migrate to the explicit data.

## Consequences

Local and remote invariants live at construction instead of being conventions repeated by
rendering, listing, documentation discovery and provider callers.
The candidate-selection service planned after this change can accept a validated identity
and provenance URI without first unpacking correlated strings.

The compatibility constructor and derived properties add a temporary adapter surface, but
they preserve existing construction and observation without preserving invalid states.
Adding another backend whose identity proves a clone location now requires an explicit
normalization rule; this is intentional because guessing from an arbitrary backend package
would violate ADR-0015's evidence boundary.
