# ADR-0045: Abandon the persistent fact cache after measuring it slower than no cache

Status: Accepted
Date: 2026-09-15
Supersedes: [ADR-0041](0041-diskcache-fact-store.md)

## Context

ADR-0041 selected DiskCache as the storage engine for persistent facts, on the premise that
repeated `maniac list` runs were re-deriving externally observed state that could safely be
reused. `feature/list-fact-cache` built that over six phases: a `FactCache` facade, persistent
provider-installation facts, short-lived local reachability facts for `man -w` and
package-page freshness, a unified upstream facade for tags, page probes, release metadata and
missing downloads, and positive npm, pipx, uv and Go source identities.

Every persistent fact carried the evidence observed while computing it, so a changed machine
would invalidate rather than misreport. Reaching that guarantee meant every provider had to
declare the files and environment its `detect()` reads, which is why the branch touched 19
production files for roughly 839 inserted lines, plus about 767 lines of tests.

Correctness hardening finished before performance was ever measured against the branch's own
base. The measurements taken during development compared the feature cold against the feature
warm, which cannot isolate the branch. ADR-0041 was accepted on an argument, not a number.

Three interleaved same-machine A/B series then ran on 2026-09-15 against base commit
`05c8463`, at 68 candidates and 68 rows constant across every run. The first was contaminated
by GitHub rate limiting and the second by a concurrent test-suite run on the same four-core
machine; the third ran alone on a quiet machine with `api.github.com` made uniformly
unreachable for both arms, and is the record. Warm-versus-warm medians, feature minus base:

| metric | 1: throttled | 2: contended | 3: of record |
| --- | --- | --- | --- |
| `total_seconds` | +0.7808 | +0.0881 | +0.9632 |
| `inventory_seconds` | +0.4170 | +1.2452 | +0.5211 |
| `local_seconds` | -0.3133 | -1.4494 | -0.2874 |
| `upstream_seconds` | +0.2995 | -1.2169 | +0.3916 |

The cache was slower on `total_seconds` in all three series. Stripping upstream,
`total_seconds - upstream_seconds` was 1.2683 base warm against 1.8976 feature warm and 1.2553
base cold against 1.9013 feature cold -- the same penalty of about 0.63 seconds whether the
cache was cold or warm, which is a fixed entry fee rather than a miss penalty. Only
`local_seconds` improved, by 0.29 seconds, which did not cover it.

## Decision

The persistent fact cache is abandoned. It is archived unmerged at `feature/list-fact-cache`
(`604581d` for the implementation, `328c93c` for the measurement record) and is not merged into
`master`, which never contained it.

ADR-0041's choice of DiskCache is superseded rather than reversed on its own terms: DiskCache
was a sound engine for the store that was built, and nothing measured here suggests a bespoke
JSON store would have done better. What failed was the premise that the store was worth having.

Three things were kept. The negative findings below are recorded here because they are the
branch's most valuable output. The tests that pin invariants of `list` which hold with or
without a cache were ported to `master` separately. The rate-limited release-metadata probe
loop that the benchmark exposed is recorded in `docs/BACKLOG.md`; it predates this branch.

The provider detection-evidence contracts, `maniac/sources/evidence.py` and `maniac/cache.py`
were not kept. They are the expensive part of the branch and they have no consumer without a
cache; `evidence.py` imports from `cache.py` and has no independent life.

## Consequences

`maniac list` keeps the process-local caching it already had -- per-root install inventories,
Cargo metadata and pipx home discovery memoized for the process, single-flight Mise registry
loading, and the file-backed repository and artifact caches. None of that was in question;
only cross-invocation persistence of derived facts was.

The negative findings are the reason to read this record before proposing a cache again:

- The raw `$PATH` inventory map cannot be validated more cheaply than it can be recomputed,
  because validating it requires the same directory scan that computes it. Persisting it saves
  only bookkeeping.
- Install-root page discovery has the same property. Proving that no matching page was added,
  removed, or became the preferred exact-name page needs the same tree walk.
- `None` is never safely cacheable. It conflates genuine absence with unreadable metadata, an
  unavailable subprocess, and a registry or network failure, and no provider could distinguish
  them. No negative provider result was ever persisted for this reason.
- Mise, local-lib and Homebrew could not be brought inside the boundary at all, because their
  composed registry, Git configuration and mutable `brew info` inputs never got complete
  evidence contracts.

Two conditions were never measured, and anyone reopening this should measure them rather than
assume the answer. Upstream probing was measured with `api.github.com` unreachable, so the
upstream fact layers paid their validation cost with nothing to return; reusing the `gh`
credential raises the request ceiling from 60 to 5000 an hour and is the one condition under
which they might still pay. And whether the fixed 0.63-second penalty is DiskCache connection
setup or per-record validation was never decomposed, which is what would decide whether a
partial cache is even viable.

All three series ran in a sandbox whose login shell had no `~/.profile`, no `~/.bashrc` and no
reachable session bus, so `login_path()` degraded to the inherited `$PATH`. Both arms degraded
identically, which preserves the comparison, but the 68-candidate workload is not proof of
what a real login produces.
