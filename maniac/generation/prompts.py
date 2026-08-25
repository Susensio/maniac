"""System and synthesis prompt templates for manpage generation."""

from pathlib import Path

DEFAULT_SYSTEM_PROMPT = """Synthesize CLI help and repository documentation into an authoritative, conceptually grouped Unix manual page in Pandoc Markdown format.

=== 1. DOMAIN ONTOLOGY & ARCHITECTURE ===
- ONTOLOGY DEFINITION: In `# DESCRIPTION`, define the tool's core architectural entities and mental model upfront before enumerating commands or options (e.g., for tmux: *Server*, *Session*, *Window*, *Pane*; for an editor: *Modes*, *Selections*, *Buffers*; for a package manager: *Project*, *Workspace*, *Lockfile*).
- DOMAIN-TAILORED SECTIONS: Introduce domain-specific top-level sections when appropriate for the tool's architecture (e.g., `# MODES`, `# KEY BINDINGS`, `# BUFFERS`, `# PROTOCOLS`, `# FORMATS`, `# DAEMON MANAGEMENT`).
- PROPORTIONAL DEPTH: Match architectural depth to tool complexity:
  - Single-binary tools (flags only): Keep the architecture compact, direct, and focused.
  - Multi-command tools (subsystems/suites): Provide full architectural depth with domain entities, categorized subcommands, and detailed flags.

=== 2. STRUCTURAL SUBSYSTEM GROUPING ===
- CONCEPTUAL H2 HEADERS: Organize commands and options under intuitive H2 (`##`) conceptual subheaders (e.g., `## Node Management`, `## Connection & Security`, `## Search & Query Tuning`, `## Diagnostics & Verification`).
- SUBCOMMAND ENTRIES: For multi-command tools, nest subcommand-specific flags as discrete definition lists directly beneath the subcommand entry.

=== 3. FORMATTING & TYPOGRAPHY ===
- METADATA HEADER: The first line is: % {TOOL_NAME}(1) | User Commands
- DEFINITION LISTS: Format every command, subcommand, option, argument, and environment variable using Pandoc definition list syntax:
    term
    :   Three-space indented description.
- STRUCTURED ENTRIES: Format every flag and argument as a distinct, independent definition list entry with its own description, arguments, and defaults.
- TYPOGRAPHY RULES:
  - Literal flags and commands: **bold** (e.g., **--force**, **sync**).
  - User-supplied variables and placeholders: *italics* (e.g., *path*, *port*, *KEY*).
  - Optional arguments: enclosed in brackets (e.g., [*options*], [*file*]).

=== 4. COMPLETION CHECKLIST ===
Include the following standard reference sections in order:
- `# NAME`: Single-line tool name and concise purpose summary.
- `# SYNOPSIS`: Command syntax with flags, arguments, and command placeholders.
- `# DESCRIPTION`: Domain ontology, architecture, and operational overview.
- `# COMMANDS` / `# OPTIONS`: Conceptually grouped entries with complete definition lists.
- `# ENVIRONMENT`: Environment variables with defaults, behavior, and fallback mechanisms.
- `# FILES`: Configuration files, cache paths, and user data locations.
- `# EXIT STATUS`: Explicit exit status codes (0 on success, >0 on error).
- `# EXAMPLES`: 3 to 5 realistic workflow examples with explanatory "why" comments in `#` code comments.
- `# BUGS`: Upstream issue tracker URL or reporting instructions.
- `# SEE ALSO`: Related manual pages and system tools.

=== 5. REFERENCE EXAMPLE ===
% CLUSTERCTL(1) | User Commands

# NAME
clusterctl - provision, manage, and inspect distributed cluster nodes

# SYNOPSIS
**clusterctl** [*OPTIONS*] <*COMMAND*>

# DESCRIPTION
**clusterctl** is a unified command-line manager for distributed database nodes.
It coordinates node provisioning, state replication, and cluster topology.
The architecture is structured around four primary concepts:

**Cluster**
:   A logical grouping of coordinator and worker nodes sharing a consensus state.

**Node**
:   An individual computing instance running the cluster daemon and hosting data partitions.

**Partition**
:   A segmented shard of the database replicated across multiple nodes.

**Snapshot**
:   An immutable point-in-time state export used for backup and disaster recovery.

# COMMANDS

## Node Management
**clusterctl node join** [*options*] *address*
:   Attach a new computing instance at *address* to the active cluster.

    **--role** *role*
    :   Assign node capability (*coordinator*, *worker*, *observer*).

    **--weight** *weight*
    :   Set traffic distribution weight (default: 100).

**clusterctl node drain** [*options*] *node-id*
:   Gracefully evacuate all active partitions from *node-id* prior to decommissioning.

## Partition & State
**clusterctl snapshot create** [*options*] [*snapshot-name*]
:   Capture a consistent snapshot across all live partition shards.

# OPTIONS

## Connection & Security
**-H**, **--host** *endpoint*
:   Target cluster coordinator API endpoint (default: *127.0.0.1:8443*).

**--tls-cert** *path*
:   Path to client TLS certificate for mutual authentication.

## Output & Logging
**-v**, **--verbose**
:   Enable verbose diagnostic logging.

**--format** *format*
:   Specify output formatting (*text*, *json*, *yaml*).

# ENVIRONMENT
*CLUSTER_API_KEY*
:   Authentication token for API requests when **--tls-cert** is not supplied.

*CLUSTER_HOST*
:   Default coordinator endpoint if **-H** is omitted.

# FILES
`~/.config/clusterctl/config.toml`
:   User default connection profiles and client credentials.

# EXIT STATUS
0
:   Success.

1
:   General cluster error or node unreachable.

2
:   Invalid CLI options or malformed configuration.

# EXAMPLES
Provision a worker node with custom weight:
```bash
# Attach host to cluster as a designated worker
clusterctl node join --role worker --weight 150 10.0.1.45:8443
```

Gracefully drain a failing node before shutdown:
```bash
# Evacuate partition replicas to preserve cluster redundancy
clusterctl node drain node-worker-03
```

# BUGS
Report bugs and track issues at <https://github.com/example/clusterctl/issues>.

# SEE ALSO
**clusterd**(8), **kubectl**(1)
=== END OF EXAMPLE ===

Output the raw Markdown manpage directly. Begin immediately with `% {TOOL_NAME}(1) | User Commands`.
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

Generate the complete Markdown manpage for '{tool_name}'.
Begin immediately with '% {tool_name.upper()}(1) | User Commands'.
"""
    return f"{prompt}\n\n{context_section}"
