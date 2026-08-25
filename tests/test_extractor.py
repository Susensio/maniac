from maniac.sources.crawler import extract_subcommands


def test_extract_from_commands_section() -> None:
    sample = """
Usage: mytool <command> [options]

Commands:
  build    Build the project
  test     Run tests
  publish  Publish package

Options:
  -h, --help  Show help
"""
    assert extract_subcommands(sample) == ["build", "test", "publish"]


def test_extract_with_cmd_name_prefix() -> None:
    sample = """
Usage: npm <command>

Commands:
  npm install  Install dependencies
  npm test     Run tests
"""
    assert extract_subcommands(sample, cmd_name="npm") == ["install", "test"]


def test_extract_actions_section() -> None:
    sample = """
Available actions:
    init       Initialize directory
    check      Check status
"""
    assert extract_subcommands(sample) == ["init", "check"]


def test_no_commands_section() -> None:
    sample = """
Usage: echo [STRING]...
Echo the STRING(s) to standard output.
"""
    assert extract_subcommands(sample) == []
