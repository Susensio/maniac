"""Where a tool's shell completion comes from (ADR-0066): shipped, or its own generator.

Two sources, never a model: a completion is code the shell runs.

- `shipped_completion`: a completion file the tool installed in its install
  root, in the handful of places release archives and packages put them.
- `generated_completion`: the tool's own generator, offered only when its
  `--help` names one (`completion <shell>`, `--show-completion <shell>`...),
  run by its resolved path from `$HOME`.

Either answer is accepted only when it looks like a completion script for
that shell and that tool (`looks_like_completion`), so a usage error, a
help page or another program's script is never installed.
"""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..logging import logger

SHELLS = ("bash", "zsh", "fish")

_GENERIC_DIRS = (
    "completions",
    "completion",
    "complete",
    "autocomplete",
    "shell-completions",
    "contrib/completion",
    "contrib/completions",
    "etc/completions",
)
"""Where a release archive puts completions, below its root or one wrapper
directory (`fd-v10.2.0-x86_64-unknown-linux-gnu/autocomplete/_fd`)."""

_SHELL_DIRS = {
    "bash": ("share/bash-completion/completions", "etc/bash_completion.d"),
    "zsh": ("share/zsh/site-functions", "share/zsh/vendor-completions"),
    "fish": ("share/fish/vendor_completions.d", "share/fish/completions"),
}
"""Each shell's own tree under a prefix-style install root."""


def file_names(shell: str, tool: str) -> tuple[tuple[str, ...], str | None]:
    """The names a `tool` completion for `shell` goes by anywhere, and the bare
    name it may also have where only that shell's files live (bash: `tool`)."""
    return _names(shell, tool, own_dir=False), (tool if shell == "bash" else None)


def _names(shell: str, tool: str, *, own_dir: bool) -> tuple[str, ...]:
    """File names a `tool` completion for `shell` goes by; a bare `tool` only in
    a directory that is bash's own, where nothing else could be meant."""
    if shell == "bash":
        named = (
            f"{tool}.bash",
            f"{tool}.bash-completion",
            f"{tool}-completion.bash",
            f"{tool}.bash_completion",
        )
        return (*named, tool) if own_dir else named
    if shell == "zsh":
        return (f"_{tool}",)
    return (f"{tool}.fish",)


def shipped_completion(root: Path, tool: str, shell: str) -> Path | None:
    """The completion for `shell` that `tool` installed in `root`, if any.

    Looked for only where completions live, never by walking the whole root
    (a uv tool's root is a virtualenv). A file is taken only when it resolves
    inside `root` -- the provider's own, as a shipped page must be -- and
    reads as a completion for that shell and tool.
    """
    if not root.is_dir():
        return None
    places: list[tuple[Path, bool]] = [
        (root / relative, True) for relative in _SHELL_DIRS[shell]
    ]
    for top in (root, *_wrapper_dirs(root)):
        for relative in _GENERIC_DIRS:
            base = top / relative
            places += [(base, False), (base / shell, True)]
    for directory, own in places:
        for name in _names(shell, tool, own_dir=own):
            candidate = directory / name
            if _usable(candidate, root, tool, shell):
                return candidate
    return None


def _wrapper_dirs(root: Path) -> list[Path]:
    """The single directory a release archive wraps its files in, if that is all
    `root` holds besides files."""
    try:
        directories = [child for child in root.iterdir() if child.is_dir()]
    except OSError:
        return []
    return directories if len(directories) == 1 else []


def _usable(candidate: Path, root: Path, tool: str, shell: str) -> bool:
    try:
        if not candidate.is_file():
            return False
        candidate.resolve().relative_to(root.resolve())
        script = candidate.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return False
    return looks_like_completion(script, shell, tool)


_COMPLETION_SUBCOMMANDS = (
    "completion",
    "completions",
    "generate-shell-completion",
    "gen-completions",
    "shell-completion",
    "shell-completions",
)
_COMPLETION_FLAGS = (
    "--show-completion",
    "--generate-shell-completion",
    "--generate-completion",
    "--gen-completions",
    "--completions",
    "--completion",
)


_UNLISTED = {
    "uv": "generate-shell-completion",
    "uvx": "generate-shell-completion",
    "ruff": "generate-shell-completion",
}
"""Tools whose generator their `--help` hides. Anything else is asked only in
a form its help names: an unknown subcommand is an argument to some tools
(`ssh completion zsh` would try a host)."""


def generator_commands(
    help_text: str, subcommands: list[str], shell: str, tool: str = ""
) -> list[tuple[str, ...]]:
    """The arguments that may make the tool print its completion for `shell`,
    most likely first; only forms its own `--help` names, or `_UNLISTED`."""
    commands: list[tuple[str, ...]] = []
    if tool in _UNLISTED:
        commands.append((_UNLISTED[tool], shell))
    for sub in _COMPLETION_SUBCOMMANDS:
        if sub in subcommands:
            commands += [(sub, shell), (sub, "--shell", shell), (sub, "-s", shell)]
    for flag in _COMPLETION_FLAGS:
        if re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", help_text):
            commands.append((flag, shell))
    # ripgrep 14: `--generate complete-zsh`.
    if "--generate" in help_text and f"complete-{shell}" in help_text:
        commands.append(("--generate", f"complete-{shell}"))
    return commands


@dataclass(frozen=True, slots=True)
class Generated:
    """A completion script the tool printed, and the arguments that made it."""

    script: str
    arguments: tuple[str, ...]


def generated_completion(
    executable: Path,
    tool: str,
    shell: str,
    *,
    help_text: str,
    subcommands: list[str],
    timeout: int,
) -> Generated | None:
    """Run the tool's own generator for `shell`, trying each form its help
    names, and return the first output that reads as a completion script."""
    for arguments in generator_commands(help_text, subcommands, shell, tool):
        script = _run(executable, arguments, timeout)
        if script is not None and looks_like_completion(script, shell, tool):
            return Generated(script, arguments)
    return None


def _run(executable: Path, arguments: tuple[str, ...], timeout: int) -> str | None:
    """Stdout of one generator run from `$HOME`, or None if it failed."""
    command = [str(executable), *arguments]
    logger.debug("Asking for a completion script", command=" ".join(command))
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
            cwd=Path.home(),
            env=os.environ | {"NO_COLOR": "1", "TERM": "dumb", "PAGER": "cat"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 and result.stdout else None


# A full script registers itself with the shell's own command; a loader
# (`just`, clap's dynamic completions) evaluates the tool's output instead:
# `eval "$(JUST_COMPLETE=bash just)"`, `JUST_COMPLETE=fish just | source`.
_SHELL_MARKS = {
    "bash": re.compile(r"(?m)^\s*complete\s+-[A-Za-z]|^\s*eval\s+\"\$\("),
    "zsh": re.compile(r"(?m)^#compdef\b|\bcompdef\b"),
    "fish": re.compile(r"(?m)^\s*complete\s+(-c\b|--command\b)|\|\s*source\b"),
}


def looks_like_completion(script: str, shell: str, tool: str) -> bool:
    """Whether `script` is a completion for `shell` that names `tool`: the
    shell's own registration command, and the tool's name, both present."""
    return bool(_SHELL_MARKS[shell].search(script)) and tool in script
