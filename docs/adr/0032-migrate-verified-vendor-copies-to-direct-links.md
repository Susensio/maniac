# ADR-0032: Migrate verified vendor copies to direct links

Status: Accepted
Date: 2026-09-14

## Context

ADR-0031 changed new verified install-root pages from durable MANIAC copies to provider-owned direct links.
Existing install-root entries still point to copies created under the old policy, so the new rule would otherwise apply only after a manual reinstall.

The manifest records each install-root's original root and the expected durable target.
That permits a conservative migration when the MANIAC copy is still unmodified and a primary page can be rediscovered below that recorded root.

## Decision

On manifest load, MANIAC migrates an existing install-root copy only when all of these hold:

- its manpath entry is the exact recorded symlink to a MANIAC-owned durable target;
- that durable target still matches the manifest checksum;
- the recorded source is an existing root; and
- the rediscovered primary page resolves beneath that root.

The migration atomically replaces the manpath link with a direct provider target, updates the checksum, and removes the superseded unshared MANIAC copy.
Modified, retargeted, missing, ambiguous, or escaping entries remain untouched.

## Consequences

Previously installed vendor pages adopt ADR-0031 without a reinstall where evidence is sufficient.
The same provider lifecycle applies after migration: removing or pruning the provider page may leave the manpath link dangling.
Conservative retention leaves unusual old entries recoverable rather than guessing at an external target.
