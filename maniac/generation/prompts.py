"""System and synthesis prompt templates for manpage generation."""

import importlib.resources
from pathlib import Path


def get_default_system_prompt() -> str:
    """Load default system prompt template from bundled markdown file."""
    try:
        return (
            importlib.resources.files("maniac.templates")
            .joinpath("system_prompt.md")
            .read_text(encoding="utf-8")
        )
    except (OSError, TypeError, ModuleNotFoundError):
        for candidate in (
            Path(__file__).parent.parent / "templates" / "system_prompt.md",
            Path(__file__).parent / "templates" / "system_prompt.md",
        ):
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        return "% {TOOL_NAME}(1) | User Commands\n"


def load_system_prompt(prompt_path: str | Path | None = None) -> str:
    """Load system prompt from file or fallback to default template."""
    if prompt_path:
        path = Path(prompt_path)
        if path.exists():
            return path.read_text(encoding="utf-8")
    return get_default_system_prompt()


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
