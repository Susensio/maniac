"""System and synthesis prompt templates for manpage generation."""

import importlib.resources
from pathlib import Path


def load_template(name: str, fallback: str = "") -> str:
    """Load template from bundled package resources or local fallback paths."""
    try:
        return (
            importlib.resources.files("maniac.templates")
            .joinpath(name)
            .read_text(encoding="utf-8")
        )
    except (OSError, TypeError, ModuleNotFoundError):
        candidate = Path(__file__).parent.parent / "templates" / name
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
        return fallback


def get_default_system_prompt() -> str:
    """Load default system prompt template."""
    return load_template("system_prompt.md", "% {TOOL_NAME}(1) | User Commands\n")


def build_synthesis_prompt(
    tool_name: str,
    help_text: str,
    doc_text: str,
    system_prompt: str | None = None,
) -> str:
    """Combine system prompt, CLI help, and doc text into the synthesis prompt."""
    base_prompt = system_prompt or get_default_system_prompt()
    prompt = base_prompt.replace("{TOOL_NAME}", tool_name.upper())

    context_section = f"""
=== CONTEXT DOCUMENTATION FOR {tool_name} ===

## CLI HELP & SUBCOMMANDS
```text
{help_text}
```

## REPOSITORY DOCUMENTATION
{doc_text}

=== END CONTEXT DOCUMENTATION ===

Generate the complete Markdown manpage for '{tool_name}'.
Begin immediately with '% {tool_name.upper()}(1) | User Commands'.
"""
    return f"{prompt}\n\n{context_section}"
