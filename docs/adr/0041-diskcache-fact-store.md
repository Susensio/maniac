# ADR-0041: Use DiskCache for persistent facts instead of bespoke JSON records

Status: Accepted
Date: 2026-09-15

## Context

`maniac list` had independent persistent upstream caches and process-local caches, while the planned local-fact cache needed readers and writers from two bounded worker pools.
Hand-writing JSON records, atomic replacement, key locking, expiry, recovery, and eviction would duplicate cache concerns throughout provider and listing code.
The same cache directory can be reached by concurrent MANIAC invocations, so thread-only synchronization was insufficient.

The user preferred a delegated cache implementation over inspectable raw files once that trade-off was explicit.


## Decision

MANIAC will use DiskCache as the persistent backing store for typed cached facts.
`maniac.cache` will be the only production facade over that dependency.

The facade will cache results at external-evidence boundaries: filesystem inventories and metadata, package-manager queries, and remote probes.
It will not persist pure derivations such as `ToolRow`; callers will cheaply recombine validated facts on each invocation.

Each fact record will retain the explicit evidence required to validate its value.
DiskCache owns atomic storage, cross-thread and cross-process access, expiry, corruption handling, and later eviction; MANIAC owns fact keys, evidence capture, validation, and the rule that transient failures never establish negative facts.

The initial backend will be `diskcache.Cache`, not sharded `FanoutCache`.
List's writes are short and sparse, while a normal cache preserves observable write failures during the first rollout.
`--no-cache` remains deferred until the fact-cache contract is implemented and benchmarked.


## Consequences

The cache directory will contain SQLite implementation files rather than files intended for direct editing.
Cache values will be JSON-compatible data, and a future inspection command can expose them without making storage layout part of the user interface.

DiskCache removes bespoke locking and atomic-write code, but it cannot decide whether a package, PATH entry, manifest, or provider metadata record still describes the machine.
Providers must therefore expose the evidence they read before their results may persist.

Existing upstream cache behavior will move behind the facade incrementally, preserving immutable versioned positives, short-lived mutable negatives, and the rule that network failures are not cached as absence.
