# Maniac Developer Workflow Justfile

set shell := ["bash", "-uc"]
set positional-arguments := true

# Show available developer recipes
default:
    @just --list

# Run test suite
test *args:
    #!/usr/bin/env bash
    set -euo pipefail
    uv run pytest "$@"

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

# Run all verification checks (linter, formatting, typing, tests)
check: lint format-check typecheck test

# Report maintainability findings; existing findings are advisory.
audit:
    uv run ruff check . --select C901,PLR0911,PLR0912,PLR0913,PLR0915 --exit-zero
    uv run complexipy . --failed --suggest-refactors --ignore-complexity

# Run the model x tool benchmark harness. Calls a real LLM -- costs money per run, not part of `check`.
bench *args:
    uv run python -m maniac.bench {{ args }}

# Cut a release: bump version, changelog, commit, tag, push.
release *args: check
    GH_TOKEN=$(gh auth token) uv run --group dev semantic-release version {{args}}
