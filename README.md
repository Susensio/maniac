# maniac

Recursively scrape and extract CLI help manuals and subcommands.

## Features

- Discovers and runs `--help` on subcommands recursively.
- Detects cycles and avoids redundant executions.
- Disables pagers and ANSI color codes to capture clean raw text output.

## Usage

```bash
# Run via python module
python3 -m maniac <command>

# Example
python3 -m maniac git
```

## Development

Run tests:

```bash
pytest
```
