# ADR-0040: Centralize verified source evidence without centralizing consumer policy

Status: Accepted
Date: 2026-09-14

## Context

Install, listing and manifest reconciliation each selected authoritative manpages and
re-derived overlapping evidence about them.
They independently chose a primary page, checked whether it resolved beneath an install
root, interpreted provider-specific direct targets, recovered repository provenance URIs
and decided which version evidence was sufficient.
The duplication made the same source eligible by convention rather than by one validated
result, while each consumer still had distinct policy that must not be collapsed.

In particular, listing classified an already reachable page before asking what was
available from an install root, and streamed remote probes behind its own eligibility and
single-flight coordinator.
Lifecycle migration had only historical manifest evidence and deliberately refused to
re-resolve a current provider.
Repository releases could also contain companion pages, each with its own exact provenance
URI under ADR-0027.

## Decision

Verified source evidence lives in `sources.candidates`, below install, listing and
lifecycle and independent of their verdict and persistence types.
It uses separate immutable install-root and repository candidate variants rather than one
record with optional fields.
A candidate page pairs one materialized path with its own exact provenance URI, and a
bundle identifies its primary page explicitly.

Install-root selection retains both the discovered page and the provider-chosen final link
target.
The service accepts a provider's alias-specific target decision, then independently
requires that the resolved final target remain beneath the inspected install root before
classifying it as provider-owned.
Ownership is a derived source-domain value; the manifest's `provider_target` boolean is a
persistence translation rather than the service contract.

Repository probing is an explicit operation separate from local install-root selection.
Remote candidates require positive version-match evidence.
Local repository sources retain their existing eligibility without manufacturing a remote
tag match, and the result keeps that absence distinct from positive evidence.
Each repository page retains the exact URI returned by documentation discovery rather than
sharing one candidate-level URI.

The service provides evidence, not a global winner.
Install keeps ADR-0016's tier order, listing consumes install-root candidates only as
availability after reachable-page classification and retains ADR-0024's remote-probe
coordinator, and lifecycle uses a deliberately weaker historical-root selector.
That historical path may centralize primary selection and containment, but it cannot
resolve a current provider, apply aliases, infer a version or relax the manifest integrity
and checksum guards around migration.

Provider implementations continue to own installer-specific alias selection and freshness.
The service owns generic page selection, final-target containment, repository-candidate
validation, per-page provenance and explicit version evidence.
Distinguishing definitive repository absence from transient probe failure remains separate
correctness work; this refactor must preserve enough evidence for that later result type
without changing fallback behavior now.

## Consequences

The three consumers share the facts that make a page a valid source without sharing their
control flow or user-facing verdicts.
An escaping provider target cannot gain provider ownership merely because a provider
returned it, and a release bundle cannot point every companion page at the primary page's
URI.

The service has more than one entry point and more than one candidate variant.
That surface is intentional: a single convenience selector would either introduce network
work into local listing classification, erase the lifecycle adapter's weaker evidence, or
recreate illegal optional-field combinations.

Install, listing and lifecycle retain integration tests for their policies while candidate
tests pin primary selection, containment, ownership, per-page provenance and version
evidence directly.
