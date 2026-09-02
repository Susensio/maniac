# ADR-0003: Use config.yaml and .env in Maniac configuration directory

Status: Accepted
Date: 2026-08-31
Supersedes: [ADR-0002](0002-provider-native-credentials.md)

## Context

The LiteLLM migration had made backend settings environment variables, while aichat demonstrated a more discoverable local configuration directory containing a small YAML file and a neighbouring `.env` file.
The user wanted the same distinction between non-secret settings and credentials, with no reuse of aichat's credentials.

## Decision

Maniac reads `config.yaml` and `.env` from `$XDG_CONFIG_HOME/maniac`.
`config.yaml` holds `backend`, `model`, and `reasoning_effort`; `.env` holds provider-native credentials.
Process environment variables override the YAML settings and are not overwritten by `.env`.

## Consequences

The default configuration is visible and editable without shell setup, while secrets remain in a separate file that is never passed as a command argument.
Maniac adds a direct YAML parsing dependency and intentionally supports only three stable LLM settings in the file until more need arises.
