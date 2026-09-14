# ADR-0035: Typed provider capabilities and a passed resolver

Status: Accepted
Date: 2026-09-14

## Context

ADR-0015 established providers as duck-typed classes with no shared base.
Two things grew on top of that shape and became load-bearing.

Callers asked for optional behavior with `getattr(provider, "latest_manpage_target", None)`.
The capability had no name, no type, and no stated ownership or freshness semantics, so every caller widened to accommodate a provider that might or might not answer.

Worse, `resolution.py` installed the composed-source resolver onto `MiseProvider` as a class attribute at import time.
Composition therefore depended on process-global state rather than on the resolver that initiated it: importing a module mutated a class, and which resolver a provider used was decided by import order rather than by the caller.

## Decision

Two `runtime_checkable` Protocols name the optional capabilities.
`DirectPageProvider` declares `direct_page_target` and `is_direct_page_target_current` together with the ownership and freshness semantics ADR-0031 requires of them.
`RoutableProvider` declares `can_detect`.
Call sites use `isinstance`, not `getattr`.

`Provider.resolve_source` gains a `sources: SourceResolver` parameter.
`ProviderRegistry` implements `SourceResolver` and passes itself down, so cross-provider composition belongs to the registry at the call site as well as in the implementation.
Importing `maniac.sources.resolution` mutates no class.

`discovery.py` keeps only Mise registry and config lookups and imports nothing upward.
`resolve_bin_path` moves to `pathcache.py` as the dependency-neutral path layer that providers and resolution can both reach.
`resolution.py` becomes purely the composition root.

A Protocol was chosen over an ABC and over a capability object.
An ABC would force all eight providers to inherit one base and carry a `return None` default, which makes "does not have this capability" indistinguishable from "has it and declines".
A capability object adds an allocation and a second indirection for two methods with no varying state behind them.

`can_detect` stays genuinely optional rather than acquiring a default.
"No route, walk the full registry" is a distinct state from "route says no", and collapsing them would change routing for a provider added later.

## Consequences

The registry owning composition does not mean the registry owning ordering, and the difference was established by a failed attempt.
Resolving the parent installation in the registry before delegating breaks Mise, whose four branches have asymmetric fallbacks: a set `inst.parent` returns the composed result even when that result is `None`, while an npm-layout parent falls through to the Mise registry.
That ordering is genuinely Mise-internal, so the resolver is passed into `resolve_source` rather than wrapping it.

Binding the registry to the provider at registration time was also rejected.
It is instance-level rather than class-level and so avoids the import-order defect, but it is still bound state where an argument would do.

`resolve_source` now takes a parameter most providers ignore, mirroring the existing precedent that `config` is passed to all providers and used by few.

The absence of import-time mutation is pinned by a test that reloads `maniac.sources.resolution` and compares `vars()` keys for every registered provider class.
Without it the callback would return the first time composition needs something a parameter cannot reach.
