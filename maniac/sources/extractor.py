"""Subcommand extraction from raw CLI help text."""

import re


def extract_subcommands(text: str, cmd_name: str | None = None) -> list[str]:
    subcommands: list[str] = []
    in_commands = False
    for line in text.splitlines():
        if not line.startswith("   ") and (
            line.rstrip().endswith(":") or line.strip().isupper()
        ):
            in_commands = "command" in line.lower() or "action" in line.lower()
            continue

        indent = len(line) - len(line.lstrip())
        if in_commands and 0 < indent <= 4:
            parts = re.split(r"\s{2,}", line.strip(), maxsplit=1)
            tokens = parts[0].split()
            if tokens:
                idx = (
                    1 if (cmd_name and tokens[0] == cmd_name and len(tokens) > 1) else 0
                )
                cmd_token = tokens[idx].rstrip(":,")
                if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", cmd_token):
                    subcommands.append(cmd_token)
    return subcommands
