# Maniac Developer Workflow Justfile

set shell := ["bash", "-uc"]
set positional-arguments := true

# Show available developer recipes
default:
    @just --list

# Run the unit suite (no external tools needed)
test *args:
    #!/usr/bin/env bash
    set -euo pipefail
    uv run pytest "$@"

# Run the integration suite against real pandoc, groff and man (ADR-0064)
integration *args:
    #!/usr/bin/env bash
    set -euo pipefail
    uv run pytest -m integration "$@"

# Run ruff linter
lint:
    uv run ruff check .

# Check code formatting
format-check:
    uv run ruff format --check .

# Run the ty type checker
typecheck:
    uv run ty check .

# Fix lint issues and format code
fix:
    uv run ruff check --fix .
    uv run ruff format .

# Run all verification checks (linter, formatting, typing, architecture audit, unit and integration tests)
check: lint format-check typecheck audit test integration

# Architecture audit: fails when structural debt grows past audit.toml's ceilings.
# `-v` lists what each metric counts; `--update` lowers ceilings after an improvement.
audit *args:
    uv run python tools/audit.py {{ args }}

# Where to start reducing complexity: complexipy's refactor suggestions (advisory).
audit-suggest:
    uv run complexipy maniac --failed --suggest-refactors --ignore-complexity

# Run the model x tool benchmark harness. Calls a real LLM -- costs money per run, not part of `check`.
bench *args:
    uv run maniac dev bench {{ args }}

# Cut a release: bump version, changelog, commit, tag, push.
release *args: check
    GH_TOKEN=$(gh auth token) uv run --group dev semantic-release version {{args}}
