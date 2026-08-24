# Maniac Developer Workflow Justfile

set shell := ["bash", "-uc"]

# Show available developer recipes
default:
    @just --list

# Run test suite
test *args:
    uv run pytest {{ args }}

# Run ruff linter
lint:
    uv run ruff check .

# Fix lint issues
lint-fix:
    uv run ruff check --fix .

# Format code
format:
    uv run ruff format .

# Check code formatting
format-check:
    uv run ruff format --check .

# Run all verification checks (linter, formatting, tests)
check: lint format-check test
