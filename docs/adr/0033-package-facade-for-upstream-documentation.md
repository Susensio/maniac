# ADR-0033: Package facade for upstream documentation

Status: Accepted
Date: 2026-09-14

## Context

`sources/docs.py` had grown to 1272 lines, the largest module in the project.
It held five unrelated concerns: repository acquisition, GitHub release retrieval, the probe cache, page selection, and documentation extraction.

Each concern wanted to gain depth for its own reasons — release retrieval needed archive validation, page selection needed companion-page identity, extraction needed prioritization — and every addition was paid for by every caller and every test, because the module was the unit of import and the unit of patching.

Six functions carried more than five arguments because `(source, binary_name, cache_dir, cfg, version, tag)` travelled as an unnamed unit through the whole file.

## Decision

`maniac.sources.docs` becomes a package whose `__init__` is the facade.

The original decision also kept every name importable from the old path, so callers were unaffected.
That requirement was mistaken -- it came from the brief, not from this decision -- and was removed once `CLAUDE.md` made explicit that no interface is frozen before 1.0.0.
The facade now exports only `fetch_and_extract_docs`, `discover_repo_manpages` and `discover_repo_manpage`, each of which has a body composing modules that must not import each other.
`resolve_repo_dir`, `discovered_manpage_uri`, `extract_docs_from_dir`, `format_docs_section` and `MAX_TOTAL_DOC_CHARS` are imported from the modules that own them.

The boundary below survived that correction unchanged, which is the useful evidence: the five-module split was right, and only the compatibility layer wrapped around it was wrong.

Five modules sit behind it in a dependency order that is acyclic by construction: `cache`, then `pages`, then `repository` and its leaf `extraction`, then `release`.
The travelling argument tuple becomes a frozen `_Probe`, so each module reads as `_Probe` to `_ProbeResult`.

A package was chosen over sibling modules named `docs_cache` and `docs_release`.
Siblings would have kept `maniac.sources.docs` a plain module and avoided a re-export layer, but they would have put five names at `sources/` top level that only mean anything together, leaving the facade indistinguishable from its parts.
The package makes the facade a location rather than a convention.

Intra-package calls stay module-qualified, as `pages._valid_page` rather than a bare imported name.
This is noisier and deliberate: bare-name imports would have made `monkeypatch.setattr(pages, ...)` silently ineffective at the call site, so a test could pass while patching nothing.

The facade retains the probe-cache lock and the tree-then-release ordering.
That ordering is ADR-0016's tier-2 policy, and pushing it down into either module would have hidden a decision inside a detail.

## Consequences

Cache, release, repository, page and extraction detail can deepen without reaching install, list or synthesis tests.

Two boundaries were forced rather than chosen, and a later reader should not mistake them for preferences.
Bounded page validation serves both the tree probe and archive validation; it lives in `pages` because placing it in `release` would have made `pages` import `release`.
Remote manpage discovery reads as page selection but is a bare-ref fetch plus `ls-tree`, so it lives in `repository`, which keeps `extraction` a leaf with no intra-package imports at all.

`_lookup_state` remains a module-level thread-local written by `cache` and read by `repository`.
The split made that hidden channel visible without removing it; removing it means threading definitiveness back as a return value through every probe signature, and is recorded in the backlog.
