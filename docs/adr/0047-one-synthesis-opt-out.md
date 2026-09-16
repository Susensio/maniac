# ADR-0047: Collapse the install tier-selection flags into one synthesis opt-out and drop forced synthesis

Status: Accepted
Date: 2026-09-16
Narrows: [ADR-0016](0016-authoritative-manpages-first.md)

## Context

[ADR-0016](0016-authoritative-manpages-first.md) fixed the source order — install root, then upstream repository, then synthesis — and put two flags on `install` to override it.
`--generate` restricted the run to tier 3; `--no-generate` restricted it to tiers 1 and 2 and never called an LLM.
That ADR named the redundancy and accepted it: "`--generate` and `--no-generate` describe the same axis from opposite ends, which is a small redundancy accepted for legibility."

Two things had changed by 2026-09-16.

The names had stopped matching the vocabulary around them.
Everything else in the codebase and in `docs/BACKLOG.md` called tier 3 *synthesis*; only the flags called it *generate*, a word that had been the command's own name until ADR-0016 renamed it to `install`.
The flags were the last surviving users of a term the rename had retired.

The asymmetry had also stopped being merely redundant.
The two flags had to be checked for mutual exclusion in the CLI layer, and they reached `run_install` as two independent booleans, `generate_only` and `no_generate`, of which three of four combinations were meaningless and one was rejected at the boundary.
An axis with two ends had become a pair of parameters with an illegal state.

`--generate`'s own value had been questioned before and left open.
`docs/BACKLOG.md` carried, under the definitive-tier-2-absence item, the sentence: "This also decides whether `install --generate` remains necessary: ADR-0016's authoritative-first order makes forcing tier 3 normally worse, but it may be the deliberate replacement escape hatch."
That was the live question — whether forcing synthesis over an authoritative page was a capability anyone needed.

ADR-0016 had already refused the thing that would make it needed.
Judging whether an existing page is bad enough to replace was deferred there and is still deferred; `docs/BACKLOG.md` carries it as a milestone gated on "a robust classification model and real output to judge."
Forcing tier 3 is only defensible as the manual override for a verdict MANIAC cannot yet reach on its own.
Without the verdict layer, `--generate` was a way to ask for a demonstrably worse page — a synthesis of `--help` in place of the manual the project itself wrote — with no evidence that the authoritative page was deficient.
Nothing in the test suite or the README exercised it for any other purpose.

## Decision

`--generate` and `--no-generate` are removed.
One flag replaces them: `--no-synthesize`, an opt-out.

Synthesis remains on by default, so ordinary `maniac install <tool>` is unchanged: tiers 1 and 2 are tried in ADR-0016's order and synthesis is the fallback when neither yields a page.
`--no-synthesize` restricts the run to tiers 1 and 2 and never calls an LLM, which is exactly what `--no-generate` did.
The deferred import of `synthesize` is kept, so `--no-synthesize` still never imports the LLM stack.

Tiers 1 and 2 are now always attempted.
There is no flag that skips them, and therefore no way to force synthesis over an authoritative page.

`run_install` takes one `no_synthesize` parameter in place of the `generate_only`/`no_generate` pair, and the CLI's mutual-exclusion check is deleted with the state it guarded.

ADR-0016's tier order and its deferral of quality judgement are untouched.
This narrows how that order is overridden; it does not reopen the order itself.

## Consequences

The illegal state is gone rather than guarded.
One boolean has two meanings and no combination to reject, so the mutual-exclusion check has nothing left to check.

The vocabulary closes.
Tier 3 is called synthesis at the CLI, in the orchestration layer and in the backlog, and the last use of "generate" as a name for it goes with the flag.

Forcing synthesis over an authoritative page is no longer possible.
This is a real capability removed, and it is removed on the argument that it was never usable for its one defensible purpose, not on the argument that nobody used it.
The cost lands the day the verdict layer arrives: whoever builds page-quality judgement will need a way to say "replace this page even though it is authoritative," and will have to reintroduce an override.
That override should be built against the verdict it serves, not inherited from a flag that predated it — which is the reason for removing it now rather than keeping it warm.

A user whose authoritative page is bad has lost the escape hatch and has one workaround: `maniac uninstall` the page, then install with the authoritative tiers unable to find it.
That is worse ergonomics for a case MANIAC has already decided it cannot detect.

Pre-1.0 and per `CLAUDE.md`, the old flags are deleted outright with no alias.
Any invocation using `--generate` or `--no-generate` fails at the CLI boundary with Typer's unknown-option error rather than silently doing something else, which is the failure mode worth having.

`docs/BACKLOG.md`'s definitive-tier-2-absence item was revised in the same period to speak in terms of `--no-synthesize` and to drop the open question this record closes; the flag it names now exists.
