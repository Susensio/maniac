# ADR-0031: Link verified vendor pages directly

Status: Accepted
Date: 2026-09-14

## Context

ADR-0028 materialized every install-root vendor page under MANIAC storage to survive provider upgrades and removals.
ADR-0030 narrowed that policy for Mise after the user preferred provider lifecycle over retained stale documentation.

The same trade-off applies to every provider installation root.
When MANIAC can prove a selected manual resolves beneath the inspected root of the currently selected binary, that provider owns the page and its lifecycle.
Copying it creates a second stale copy without making the tool itself available after its provider removes the version.

## Decision

An install-root page whose resolved path is contained by its selected installation root is linked directly from the manpath.
MANIAC records it as a provider-owned target and never deletes it on uninstall.

Mise retains ADR-0029's stronger `latest` behavior when that alias resolves to the same root as the running binary.
When it does not, the generic direct concrete-root target applies and `list` marks the divergent alias state as `outdated`.

Repository and synthesis pages retain durable MANIAC storage because their source is a disposable cache or MANIAC's own output.
An install-root candidate outside its inspected root likewise retains the durable fallback rather than receiving an unverified direct link.

Existing materialized vendor pages remain in place until a normal reinstall can replace them.
Automatic retargeting would need source-relative information that old entries do not retain and would defeat the simpler policy.

## Consequences

MANIAC does not retain a second copy of a verified provider-owned vendor manual.
Removing or pruning that provider version can leave the corresponding manpath link dangling, which reflects that the tool and its manual have left together.

The direct-link rule is evidence-based rather than provider-name-based, so providers do not need a bespoke lifecycle alias to avoid a copy.
