# ADR-0054: Refuse synthesis, don't prompt, when the tier-2 probe itself fails

Status: Accepted
Date: 2026-09-22
Narrows: [ADR-0016](0016-authoritative-manpages-first.md), [ADR-0047](0047-one-synthesis-opt-out.md)

## Context

[ADR-0016](0016-authoritative-manpages-first.md) fixed the tier order -- install root, then upstream repository, then synthesis -- and made synthesis the fallback whenever tier 2 yields no page.
It never distinguished two reasons tier 2 can yield nothing: the repository was checked and genuinely has no manpage for this tool at this version, or the check itself did not complete -- a network error, a git failure, an unreadable tree, a tag lookup that could not run.
Both reached the same `[]` and both fell through to synthesis identically.

`docs/BACKLOG.md` carried this as an open item since at least the 2026-09-14 architecture wave, naming the risk plainly: a transient probe failure "must not silently fall through to synthesis, which can conceal a wrong repository or a network, tag, tree, release, or validation failure."
It named the mechanism gap too.
`docs.cache`'s module-level `_lookup_state` thread-local (introduced by ADR-0033's split of `cache` from `repository`) let a callee report definitiveness to its immediate caller, but nothing threaded that bit any further -- `discover_repo_manpage(s)`, the function every tier-2 caller actually calls, returned `list[Path]` and dropped it at the boundary.
Reporting the distinction outward required removing the thread-local first, not around it.

The backlog item left one question open on top of the mechanism: "then decide between interactive confirmation and uniform refusal."
That question is what this record settles.

Both items were picked up together in this session (2026-09-22), alongside two adjacent backlog items rejected on the same pass for resting on a premise MANIAC does not have -- a long-running process and concurrent access to the manpath -- which this machine's solo, single-invocation, single-user use never exercises.
Those two are recorded as settled exclusions in `docs/BACKLOG.md`, not here; this record is about the two that were real.

## Decision

A non-definitive tier-2 probe refuses the install rather than falling through to synthesis, and rather than asking.

No interactive prompt.
MANIAC already has a refusal convention -- `InstallRefused`, established by ADR-0020 and given its exit-status rule by [ADR-0048](0048-refusal-exits-non-zero.md) -- and this reuses it rather than adding a second one.
The message names what was checked and why it isn't a verdict: which repository (`source.identity`) was consulted, that it was the tier-2 check specifically, and that the check failed rather than returning a definitive answer.
The existing `--no-synthesize` opt-out (ADR-0047) is unaffected -- it already means "accept tiers 1-2 only," and still means exactly that; the new refusal only fires on the path that would otherwise call synthesis, which `--no-synthesize` skips before reaching.

Reaching this required threading `.definitive` through, not around, the whole chain: `_find_matching_tag` and `_download` now return it as part of their result tuple instead of writing `_lookup_state`, which is deleted; `discover_repo_manpage`/`discover_repo_manpages` return it at their public boundary instead of dropping it; `select_repository` and `_try_repository` carry it up to `run_install`, which is where the refusal decision is made. `tuple[<value>, bool]` throughout, matching `_ProbeResult`'s existing `pages, definitive` shape and `_download_result`'s prior convention, rather than exposing the private `_ProbeResult` dataclass itself at the public boundary.

Definitive absence is untouched: it still falls through to ordinary synthesis exactly as ADR-0016 specified.
This decision is only about the case that used to look identical to it and isn't.

## Consequences

A tool whose tier-2 check fails transiently now stops instead of getting a synthesized page that a working check might have made unnecessary.
The user reruns once the check can complete, or passes `--no-synthesize` to accept whatever tiers 1-2 already found -- the same two options ADR-0048's refusal convention already gives every other refusal, so this adds no new vocabulary for a caller to learn.

Interactive confirmation was rejected, not merely not built.
MANIAC's other refusals (ADR-0020's unreachable binary, ADR-0016's unmanaged-destination collision) do not prompt either; adding a prompt here would have made tier-2 probe failure the one refusal in the codebase with a different shape, for a case no more urgent than the others.
Refusal also composes with scripted use the way ADR-0048 already established prompting cannot.

The cost is a new way for `install` to stop short that a flaky network can trigger where none existed before.
This is accepted because the alternative -- silently synthesizing -- is exactly the concealment the backlog item was written against: a wrong repository, a bad tag match, or a dropped connection would have produced a plausible-looking page with no signal that tier 2 was never actually consulted.

`docs/BACKLOG.md`'s definitive-tier-2-absence item is closed by this record and the commit implementing it.
