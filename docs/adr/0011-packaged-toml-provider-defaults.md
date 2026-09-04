# ADR-0011: Replace the backend setting and model aliases with packaged TOML provider defaults

Status: Accepted
Date: 2026-09-04
Supersedes: [ADR-0003](0003-config-yaml-and-env.md)

## Context

ADR-0003 kept `agy`, a subprocess CLI, as a second LLM backend beside the LiteLLM API, encoding the choice as a `backend` key in `config.yaml` alongside `model` and `reasoning_effort`.
The code carried one model alias table per backend.

Both tables were hand-maintained and lossy.
`flash-high` and `flash-medium` both resolved to `gemini/gemini-3.7-flash`, because the high/medium/low distinction was reasoning effort rather than model identity; `Config.resolve_reasoning_effort` compensated by inferring a default from a `gemini/` prefix on the resolved model name.
The two namespaces had already drifted apart: `bench/harness.py` held agy display names in `DEFAULT_MODELS` and `JUDGE_MODEL`, which the LiteLLM table did not resolve, so `just bench` without an explicit `--model` passed an unusable identifier through to LiteLLM.

Three findings settled the direction.
`litellm-acp` (github.com/Susensio/litellm-acp) exposed ACP agents as a LiteLLM provider callable as `model="acp/claude"`, removing the reason a subprocess-CLI backend needed a code path of its own.
LiteLLM 1.98.0 accepted `reasoning_effort` as a unified parameter, mapping it per provider — OpenAI natively, Anthropic to `output_config.effort`, Gemini to `thinking_level` — so it was a MANIAC-level setting rather than a per-provider one.
`pyyaml` was used in exactly one place, `config.py`, while `tomllib` was already used across `sources/discovery.py`.

Deriving a default model from LiteLLM's own registry was weighed and rejected.
`litellm.model_cost` carries pricing, context window and capability flags but nothing expressing "recommended" and no release date, so a derived pick would have been arbitrary and would have changed silently when LiteLLM refreshed its map.
Resolving a family name such as `sonnet-latest` to a concrete model was rejected on the same grounds: the registry held seven Anthropic sonnet names across four naming schemes, including one model under two spellings (`claude-4-sonnet-20250514`, `claude-sonnet-4-20250514`), and a wrong guess would have silently used a different model.
Providers that publish their own floating alias were found to differ: Gemini shipped `gemini-flash-latest` and `gemini-pro-latest`; Anthropic shipped none.

## Decision

MANIAC has no backend concept.
The `agy` integration, both alias tables, the `backend` setting and every branch on it are removed; `model` is a LiteLLM model identifier and nothing translates it.
ACP becomes reachable by setting `model` to an `acp/…` identifier, which requires no MANIAC code.

Configuration moves to TOML.
A `defaults.toml` packaged in the wheel holds a flat `[providers]` table mapping provider to default model, plus limits and timeouts; `$XDG_CONFIG_HOME/maniac/config.toml` holds the user's `provider`, optional `model` and optional `reasoning_effort`; `.env` continues to hold credentials.
Resolution runs one chain: CLI flag, then user config, then environment, then the first provider in `[providers]` declaration order whose API key is present, then that provider's default model.

A provider's default is the vendor's own floating alias where one exists and a pinned identifier where it does not, validated at use against `litellm.models_by_provider` so a dead pin reports itself by name.
`reasoning_effort` is a single optional global setting, sent only when `litellm.supports_reasoning` reports the model accepts it.

MANIAC never prompts interactively.
No API key present is a hard error naming the config path and the variables checked; exactly one is used silently; more than one takes declaration order and writes a single line to stderr naming the provider chosen and how to pin it.

## Consequences

The project drops a dependency and is left with one config parser instead of two.
Adding a provider is a line in `[providers]` rather than an entry in two alias tables plus a branch.
The `gemini/` prefix test and the drift that made `just bench` fail both become unrepresentable.

Refusing to prompt keeps MANIAC usable inside a pipe, at the cost of a first run that fails rather than guiding an unconfigured user.
The packaged provider table is a maintenance item that will go stale; validating against the registry converts staleness into a named error instead of an opaque API failure, but does not prevent it.
Existing `config.yaml` files need a manual migration of three keys, and anyone using the agy backend loses it until `litellm-acp` is installed and `model` is repointed.
