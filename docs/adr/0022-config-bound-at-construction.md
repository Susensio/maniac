# ADR-0022: Bind configuration once at first construction instead of at module import

Status: Accepted
Date: 2026-09-10

## Context

MANIAC bound configuration three different ways at once, with no stated reason for the split.

`config.py` read the XDG directories into module-level constants (`_XDG_CONFIG`, `_XDG_CACHE`, `_XDG_DATA`, `_XDG_STATE`) at first import, and the user's `config.toml` into `_CONFIG_VALUES` the same way.
The packaged `defaults.toml` limits and timeouts were bound harder still: they were used as class-body defaults on `Config`, so they were evaluated once when the class object was created.
Against that, environment variables were read live — `MANIAC_LLM_API_KEY` and the reasoning effort per `Config()` instantiation, `MANIAC_MODEL` inside `resolve_model` on every call.
`sources/discovery.py` read `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` from `os.environ` on every call, in two places.

This surfaced as what looked like duplication: `discovery.py` computing XDG paths that `Config` already owned, recorded in `docs/BACKLOG.md` as a consolidation to be done.
Investigating it inverted the entry.
The two formulas were byte-identical — same variable, same precedence, same default, neither calling `expanduser` — and differed only in when they ran.
Routing `discovery` through `Config` would therefore have removed no duplication; it would have silently stopped `discovery` honouring a variable set after `maniac.config` was first imported.
`cli/__init__.py` built a module-level `default_cfg = Config()` at import, so in the CLI that import always happened first.

No production behaviour was wrong at the time this was written.
Nothing under `maniac/` mutated the XDG variables at runtime — `os.environ` was written in exactly one place, for an unrelated LiteLLM pricing-map setting — so a real invocation had its environment fixed before the process started and every reading agreed.

The cost was paid by the tests instead, and it was visible in the suite's shape.
`tests/conftest.py` carried an autouse fixture that monkeypatched the frozen module globals directly, with a comment recording why: an ordinary env-var monkeypatch after import had no effect.
The comment also noted the leak reappeared at any entry point that built a default `Config`.
So the suite had reached past the public interface to do an ordinary thing, and a test that set `XDG_CONFIG_HOME` steered `discovery` but not `Config` — a trap rather than a convenience.

Two readings of "read once" were available and were being conflated.
Reading a value once per process is a claim about coherence: every part of one run sees the same configuration.
Reading it at import time is a claim about a specific moment, and that moment is the earliest one available and the only one the program cannot intervene in — the value is fixed before `main` exists, before a flag is parsed, before a test can arrange anything.
The first is what a short-lived CLI wants; the second is merely how the first had been implemented.

Making everything live was weighed and rejected.
The risk it introduces is not a thread race — MANIAC is short-lived and single-threaded — but incoherence inside one run: `resolve_model` consulting `MANIAC_MODEL` at a different moment than the `Config` it belongs to was built, so two parts of one invocation act on different configurations with nothing reporting the disagreement.
That argues for freezing more, not less.

## Decision

Configuration is bound once per process, at the moment the first `Config` is constructed, not at module import.

The XDG directories are resolved inside `Config`'s field factories rather than read from module-level constants, so constructing a `Config` under a given environment yields a `Config` that reflects it.
The user's `config.toml` and the packaged `defaults.toml` are read on the same occasion rather than at import, and the packaged limits and timeouts stop being class-body defaults.
`resolve_model`'s consultation of `MANIAC_MODEL` is bound at construction with everything else, so ADR-0011's resolution chain — flag, user config, environment, provider sniff — is evaluated once against one environment instead of partly at import and partly per call.

The CLI constructs one `Config` at entry and threads it; module-level `Config()` instances at import time are removed.
`sources/discovery.py` takes its XDG directories from that `Config` rather than reading `os.environ` itself, which is now safe because `Config` no longer freezes them before the process can act.

Read-once semantics are kept deliberately and are the point of the record: this narrows when the read happens without making any value live.

## Consequences

`Config` becomes steerable by the environment, so `tests/conftest.py`'s autouse fixture can stop monkeypatching module internals and set variables the ordinary way.
The class of bug that fixture existed to contain — an entry point building a default `Config` and leaking writes into the developer's real `~/.local/state/maniac` — stops depending on every future entry point remembering to avoid it.
A test that sets `XDG_CONFIG_HOME` now steers the whole program rather than half of it.

The change touches every `Config` consumer, which is its real cost, and the risk concentrates in the entry points that currently build one at import: those become lazy, and anything that captured `default_cfg` at import time captures a different object or none.
Threading one `Config` is more typing at each call site than reaching for a module global, and nothing in the language prevents a future caller from constructing a second `Config` — one built after the environment changed would disagree with the first, which is the failure this decision makes unlikely rather than impossible.

ADR-0011's model validation against `litellm.models_by_provider` moves from per-call to per-construction.
A dead pin still reports itself by name, but it does so once, when the `Config` is built, rather than at each use — earlier, and in one place, which is the better moment for it, but a different moment than before.

Import time stops being load-bearing, which means `import maniac.config` no longer reads the filesystem or the environment as a side effect.
That makes the module cheaper to import and safe to import from a test that has not yet arranged its environment.

## Corrections

2026-09-10: The consequences originally said model validation moved from each resolution call to `Config` construction.
That timing cannot coexist with ADR-0011's flag-first precedence: an invalid lower-priority configured or environment model would abort construction before a valid explicit model could win.
Configuration values and provider availability bind at construction; validation happens once the precedence winner is selected by `resolve_model`.
