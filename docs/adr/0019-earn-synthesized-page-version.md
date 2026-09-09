# ADR-0019: Earn a synthesized page's recorded version from version-matched inputs instead of stamping the installed binary's

Status: Accepted
Date: 2026-09-09

## Context

[ADR-0018](0018-list-reports-manpage-reachability.md) gave the manifest a version field so `list` could report an `outdated` state, and required that state to rest on positive evidence: a recorded page version differing from the binary's.
It rejected stamping pre-existing entries with the current binary version, on the stated grounds that doing so "asserts a freshness nobody checked".
That reasoning was applied only to migration. What a *new* entry's version would mean was not examined.

Implementing ADR-0018's offline core exposed the gap.
The version field was threaded from `install_manpage` into the manifest, and tiers 1 and 2 passed `inst.version`.
Tier 3 could not: `run_pipeline` took a `tool_name: str` and had no `Installation` in scope at its `install_manpage` call, so it recorded no version at all.
Both manifest entries on the development system were `tier: synthesis`, so `outdated` was unreachable for exactly the pages MANIAC owned.

The obvious repair — give `run_pipeline` the installed version and record it — was wrong for a reason the tier-3 pipeline made visible only on inspection.
Synthesis assembled its context from three inputs, and they did not agree about version.
`find_subcommands` crawled the installed binary's own `--help`, which was version-true by construction.
`fetch_and_extract_docs` called `resolve_repo_dir` with no version argument, so repository documentation came from the default branch rather than a matching tag — the version-matched clone path existed and was already used by tier 2, and tier 3 simply never passed the version through it.
`_fetch_github_wiki_docs` fetched wiki content, which carries no version at all.

Stamping `inst.version` onto a page synthesized partly from default-branch documentation would therefore have manufactured precisely the false claim ADR-0018 refused.
It would also have been the second place the project made that mistake: [ADR-0016](0016-authoritative-manpages-first.md) had already faced it at tier 2 and chosen to refuse outright, falling through to synthesis rather than installing an upstream page from the default branch under a version it could not support.

The two entries in question both had exact upstream tags for their installed versions, under differing conventions — `sigoden/aichat` at `v0.30.0` and `astral-sh/ty` at `0.0.78` — and `_find_matching_tag` already tried both the `v`-prefixed and bare forms.
So the version-matched path was reachable for them, and the question was what to record when it was not.

## Decision

A recorded version is earned by the documentation that produced the page, never asserted from the binary that happens to be installed.

Tier 3 passes the installed version into its documentation extraction, so `resolve_repo_dir` resolves a matching tag and clones it, exactly as tier 2 already does.
Where a matching tag is found, the repository documentation is version-matched and the entry records that version.
Where no matching tag is found, extraction falls back to the default branch and the entry records no version.

Tier 3 falls back rather than refusing, which is where it parts from ADR-0016's tier 2.
Tier 2 refuses because it is choosing whether to install someone else's page as authoritative, and an unmatched version makes that claim unsupportable.
Tier 3 is the last resort and has nothing to fall through to, so refusing would mean generating nothing at all.
The page is still worth having; it is the version claim that is not, and dropping the claim while keeping the page costs nothing that matters.

A page recorded with no version reads `ok` forever and can never read `outdated`.
That is ADR-0018's "absent evidence reads ok" rule operating as designed rather than a degradation, and it is the same permanent condition `local_lib` already sits in, never reporting a version at all.

The GitHub wiki stays in the context of a version-claiming synthesis, and the recorded version does not assert that every byte of the page came from the matched tag.
It records the tool version the page was generated against.
Excluding the wiki whenever a version was claimed was weighed and rejected: it would have cost real documentation on exactly the pages MANIAC knows most about, to buy a precision the field does not need.
The field exists so a binary moving ahead of its page can be detected, and a page built from a matched tag, that tag's `--help`, and a wiki snapshot is the page for that version in the only sense the comparison uses.

## Consequences

`outdated` becomes reachable for synthesized pages, which is what ADR-0018 needed and could not have.
Repairing the two existing entries now produces true versions rather than plausible ones, so the repair is worth performing rather than merely permitted.

Tier 3 gains a network dependency it did not have in the same shape: it already cloned a repository, but now resolves a tag first via `git ls-remote`, and clones the tag rather than the default branch.
Cached resolution under `<name>@<tag>` already existed for tier 2 and is shared, so repeated synthesis of the same version costs no additional network.

Two pages generated from the same repository at different tool versions no longer share a cache directory, which is correct and also means the cache grows per version rather than per tool.

A tool whose upstream tags do not follow either convention `_find_matching_tag` tries gets a page with no recorded version, silently.
The page is unaffected; only its future staleness detection is.
How often that happens is unmeasured, and is the same open question `docs/BACKLOG.md` already records against tier 2's refusal — the measurement now covers both, and matters more than it did, because here the cost is a silent capability loss rather than a visible fallthrough.

The version recorded for a page and the version of the binary at the moment of install can differ, if a tool is upgraded between detection and synthesis.
This was not treated as worth guarding: the window is a single command's runtime, and the failure mode is one spurious `outdated` row that a reinstall clears.
