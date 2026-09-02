# ADR-0001: Use LiteLLM API with explicit agy fallback

Status: Superseded by [ADR-0002](0002-provider-native-credentials.md)
Date: 2026-08-31

## Context

MANIAC had used the `agy` subprocess for both manpage synthesis and LLM evaluation.
That coupled the application to a separately installed CLI and its account quota.
The user needed a dedicated API key that was independent of their other LLM tooling.

## Decision

LiteLLM was selected as the primary backend.
MANIAC receives its key through `MANIAC_LLM_API_KEY`, uses provider-qualified model IDs, and defaults Gemini Flash reasoning to `low`.
The `agy` backend remains available only when `MANIAC_LLM_BACKEND=agy` is selected explicitly.
MANIAC does not automatically fall back from an API error or quota limit to `agy`.

## Consequences

MANIAC installs a larger Python dependency but no longer requires an LLM CLI for the default path.
Generation and evaluation use the same configured API key and consume the same provider-project quota.
An API key never appears in process arguments or MANIAC configuration files.
Users who require an account or model supported by `agy` can opt into the legacy backend, but must install and configure it themselves.
