# Maniac Justfile

set shell := ["bash", "-uc"]

# Show available recipes
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

# Crawl a CLI command and its subcommands
crawl +cmd:
    uv run python -m maniac crawl {{ cmd }}

# Extract repository documentation for a tool
docs tool:
    uv run python -m maniac docs {{ tool }}

# Generate a synthesized manpage for a tool
generate tool:
    uv run python -m maniac generate {{ tool }}

# List executables in ~/.local/bin lacking manpages
list-missing:
    uv run python -m maniac list-missing
