# ADR-0038: One resolved tool through every install tier

Status: Accepted
Date: 2026-09-14

## Context

`run_install` took thirteen arguments and `run_pipeline` twelve across fifty-eight statements.
Worse than their size, they duplicated work: `run_pipeline` called `discover_repo()` and `find_installation()` again for a tool `run_install` had already resolved, so an install reaching tier 3 resolved the same facts twice and could in principle reach different answers on the second pass.

Version-match evidence, which ADR-0019 makes load-bearing for a synthesized page's recorded version, was therefore assembled in two places from two resolutions.

## Decision

`ResolvedTool` carries the selected binary, its installation, provider, installed version and canonical documentation source from the first resolution through every tier.

It lives in `orchestration/`, not `models.py`. It holds a `Config` and reaches into `sources.resolution`; that makes it orchestration state rather than a domain type, and putting it among the domain models would have implied a purity it does not have.

`resolve_tool()` is its only constructor, which makes the two entry paths structural rather than conventional:
`install` resolves once and hands the result down, and direct synthesis calls `resolve_tool()` explicitly.
`synthesize(tool, ...)` replaces `run_pipeline` and imports nothing that can resolve, so it cannot silently re-resolve facts it was given.

`run_pipeline` is deleted rather than kept as a forwarding wrapper. It was a layer that only called two others, and nothing before 1.0.0 obliges us to keep its name alive.

`documentation_source` is a lazy `cached_property` rather than an eager field.
Resolving it eagerly would make tier 1 pay provider source resolution it has never paid, which is a change in cost wearing the clothes of a change in shape.

`orchestration/__init__` no longer re-exports the pipeline, so importing the install entry point does not pull the LLM stack in behind ADR-0016's promise that `--no-generate` performs no synthesis work.

## Consequences

`synthesize` takes nine arguments where `run_pipeline` took twelve, and its fifty-eight statements are split across six named helpers.
A test asserts `find_installation` and `resolve_source` each run exactly once during an install reaching tier 3, with `discover_repo` patched to raise so a reintroduced second resolution fails loudly rather than quietly costing time.

Two designs were rejected for correctness rather than taste.
An optional `tool: ResolvedTool | None = None` parameter would have read as re-resolution smuggled behind a default, and would have made "the caller supplied facts" indistinguishable from "the caller forgot".
Folding `--output-dir` and `--cache-dir` into a `dataclasses.replace(cfg, ...)` at the CLI would have removed two more arguments, but constructs a second `Config` and re-runs its environment binding, against ADR-0022's one-binding-per-process reasoning.

`run_install` still takes eleven arguments, one over the repository's threshold.
The clean fix collapses the mutually exclusive `generate_only` and `no_generate` booleans into one tri-state tier policy, which changes CLI semantics and is therefore not a refactor's to make.
