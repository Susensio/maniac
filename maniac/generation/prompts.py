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
    *,
    seed_text: str | None = None,
    seed_label: str | None = None,
) -> str:
    """Combine system prompt, CLI help, doc text and an optional seed page.

    The seed is a page people wrote for another version of the same software
    (`candidates.seed_for`): structure, prose and examples to start from,
    never the authority on what the tool accepts today.
    """
    base_prompt = system_prompt or get_default_system_prompt()
    prompt = base_prompt.replace("{TOOL_NAME}", tool_name.upper())
    seed_section = (
        _seed_section(tool_name, seed_text, seed_label or "version unknown")
        if seed_text
        else ""
    )

    context_section = f"""
=== CONTEXT DOCUMENTATION FOR {tool_name} ===

## CLI HELP & SUBCOMMANDS
```text
{help_text}
```

## REPOSITORY DOCUMENTATION
{doc_text}
{seed_section}
=== END CONTEXT DOCUMENTATION ===

Generate the complete Markdown manpage for '{tool_name}'.
Begin immediately with '% {tool_name.upper()}(1) | User Commands'.
"""
    return f"{prompt}\n\n{context_section}"


def _seed_section(tool_name: str, seed_text: str, seed_label: str) -> str:
    return f"""
## AN EXISTING MANUAL PAGE FOR ANOTHER VERSION: {seed_label}
People wrote this page for {tool_name}, but not necessarily for the version
installed now. Use it as a starting point: its structure, its explanations
and its examples are worth keeping. The CLI help above is the authority on
what this version accepts: where they disagree, follow the help; leave out
options, commands and defaults the help no longer shows; add what the help
shows and the page lacks.
```roff
{seed_text}
```
"""
