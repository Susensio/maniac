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
