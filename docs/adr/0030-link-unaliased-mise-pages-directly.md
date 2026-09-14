# ADR-0030: Link unaliased Mise pages directly

Status: Accepted
Date: 2026-09-14

## Context

ADR-0029 used a validated Mise `latest` alias when it represented the running global binary.
When the alias could not be validated, it retained ADR-0028's durable MANIAC copy.

That copy preserved documentation after Mise removed a concrete version, but duplicated provider-owned data and kept a manual page for a tool version the user had removed.
A global version deliberately different from `latest` is uncommon and already visible to MANIAC as stale state.

## Decision

Mise install-root pages always use a direct provider-owned target.
When the sibling `latest` alias resolves to the same concrete root as the executable, MANIAC links through `latest` so normal global upgrades advance the page.
Otherwise it links directly to the selected page under the concrete installation root.

The concrete fallback is recorded as a provider target, so uninstall removes only MANIAC's manpath link and never the Mise page.
It is not materialized under MANIAC data storage.
`list` continues to mark a direct page whose `latest` alias does not validate against the inspected binary as `outdated`, making an intentional pin or divergent layout visible.

Existing materialized Mise vendor pages remain durable until a normal reinstall can replace them.
Automatic retargeting would need source-relative information that those entries do not retain and would reintroduce complexity this decision removes.

## Consequences

MANIAC no longer retains a fallback copy of a Mise page after the user removes or prunes its selected version.
The corresponding manpath link may become dangling, which is an acceptable reflection of the provider-owned target disappearing.

Other providers keep ADR-0028's durable-materialization behavior until they provide comparable lifecycle evidence.
