# ADR-0002: Use provider-native credentials alongside MANIAC key override

Status: Superseded by [ADR-0003](0003-config-yaml-and-env.md)
Date: 2026-08-31
Supersedes: [ADR-0001](0001-litellm-api-backend.md)

## Context

ADR-0001 made LiteLLM the primary backend but its dedicated `MANIAC_LLM_API_KEY` requirement stopped LiteLLM from discovering credentials using each provider's standard environment variable.
The user wanted to keep a Gemini API key in Maniac's configuration directory without turning the backend into a Gemini-only integration.

## Decision

Maniac autoloads `$XDG_CONFIG_HOME/maniac/.env` without overwriting the process environment.
LiteLLM receives `MANIAC_LLM_API_KEY` only when it is explicitly configured; otherwise it discovers the configured model's provider-native credential, such as `GEMINI_API_KEY` or `ANTHROPIC_API_KEY`.
Provider-qualified LiteLLM model IDs remain the interface for selecting a provider.

## Consequences

One configuration file can hold credentials for several LiteLLM providers, and switching models need not change Maniac code or rename a key.
Credential-validation errors now originate from LiteLLM because Maniac cannot know which provider credential a custom model requires.
`MANIAC_LLM_API_KEY` remains useful for compatible providers that need a deliberate per-Maniac override.
