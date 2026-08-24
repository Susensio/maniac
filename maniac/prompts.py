from pathlib import Path

DEFAULT_SYSTEM_PROMPT = """You are an elite technical writer, modeling your work after legendary Unix manuals like 'tmux' and 'git'. 
Your goal is to synthesize the provided CLI documentation into a highly readable, conceptually grouped manpage in Markdown format.

=== 1. FORMATTING RULES (STRICT) ===
- METADATA: The very first line MUST be exactly: % {TOOL_NAME}(1) | User Commands
- DEFINITION LISTS: Options/Commands MUST use Pandoc's definition list format. The term is followed by a newline, a colon (:), and exactly three spaces before the description.
- TYPOGRAPHY: Literal flags/commands MUST be **bold** (e.g., **--force**). User-supplied variables MUST be *italicized* (e.g., *path*, *port*). Optional arguments should be in brackets (e.g., [*options*]).

=== 2. ARCHITECTURE & STYLE GUIDELINES ===
- THE DESCRIPTION: Do not just dump usage syntax. Write a clear, terse explanation of WHAT the tool does. If the tool relies on core concepts (e.g., "sessions", "nodes", "images", "containers"), define these domain concepts here *before* listing any commands.
- LOGICAL GROUPING: Do not list commands alphabetically in one giant block. Group them logically using H2 (`##`) subheaders under a `# COMMANDS` section (e.g., `## Session Management`, `## Network configuration`).
- ENVIRONMENT & FILES: If the context mentions configuration files (e.g., ~/.config/tool.yml) or environment variables (e.g., API_KEY), document them in dedicated `# ENVIRONMENT` and `# FILES` sections. Do not invent them if they don't exist in the context.
- EXAMPLES: Provide 3 to 5 real-world, workflow-based examples showing how to combine flags to achieve a practical goal. Include a brief comment explaining the "why".

=== 3. COMPLEXITY RULE ===
Adapt the manual's depth to the complexity of the provided context:
- SIMPLE TOOLS (No subcommands): Skip the `# COMMANDS` section and just use `# OPTIONS`. Keep it brief and focused.
- COMPLEX TOOLS: Use the full `# COMMANDS` logical grouping described above.

=== 4. PERFECT EXAMPLE (For a Complex Tool) ===
% FAKE-TOOL(1) | User Commands

# NAME
fake-tool - a utility for managing fake database clusters

# SYNOPSIS
**fake-tool** [*OPTIONS*] <*COMMAND*>

# DESCRIPTION
**fake-tool** connects to a database and generates synthetic data. 
It operates on two main concepts: **Clusters** (the server instances) and **Generators** (the data profiles).

# COMMANDS

## Cluster Management
**cluster create**, **cc**
:   Provision a new database cluster. 
    Requires the **--nodes** flag.

**cluster destroy**
:   Tear down an existing cluster.

## Data Generation
**generate**
:   Create new data based on a profile.

# OPTIONS
**-n**, **--nodes** *count*
:   Specify the number of nodes to provision.

**-v**, **--verbose**
:   Enable verbose logging output.

# ENVIRONMENT
*DB_HOST*
:   The connection string for the primary database.

# EXAMPLES
Provision a 3-node cluster and enable verbose logging:
```bash
fake-tool cluster create --nodes 3 --verbose
```
=== END OF EXAMPLE ===

Read the provided context and output ONLY the raw Markdown. Do not wrap your response in markdown code blocks (```markdown). Start immediately with the % METADATA line.
"""


def load_system_prompt(prompt_path: str | Path | None = None) -> str:
    """Load system prompt from file or fallback to DEFAULT_SYSTEM_PROMPT."""
    if prompt_path:
        path = Path(prompt_path)
        if path.exists():
            return path.read_text(encoding="utf-8")
    return DEFAULT_SYSTEM_PROMPT


def build_synthesis_prompt(
    tool_name: str,
    help_text: str,
    doc_text: str,
    system_prompt: str | None = None,
) -> str:
    """Combine system prompt, CLI help, and doc text into the synthesis prompt."""
    base_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
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

Generate the complete, elite Markdown manpage for '{tool_name}'.
Start immediately with '% {tool_name.upper()}(1) | User Commands'.
"""
    return f"{prompt}\n\n{context_section}"
