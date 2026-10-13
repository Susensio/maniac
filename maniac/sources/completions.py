"""Where a tool's shell completion comes from (ADR-0066): shipped, or its own generator.

Two sources, never a model: a completion is code the shell runs.

- `shipped_completion`: a completion file the tool installed in its install
  root, found by name anywhere in it (`completion_paths`).
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
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..logging import logger

SHELLS = ("bash", "zsh", "fish")

_SKIPPED_DIRS = {
    "test",
    "tests",
    "testdata",
    "fixtures",
    "vendor",
    "node_modules",
    "site-packages",
    "__pycache__",
    "third_party",
    "examples",
    ".git",
}
"""Never a tool's own completion: test fixtures, vendored trees, a
virtualenv's packages (a uv tool's root is one), dependencies' modules."""

MAX_DEPTH = 6
"""Directories below the root a completion may sit in, counting the file:
`fd-v10.2.0-x86_64-unknown-linux-gnu/autocomplete/_fd` is three."""


def file_names(shell: str, tool: str) -> tuple[str, ...]:
    """The names a `tool` completion for `shell` goes by, wherever it sits.

    Besides the tool's own (`_tool`, `tool.fish`, `tool.bash`...), the
    generic `completion.<shell>` some projects ship (aichat, herdr): inside
    a tool's own install root or repository it can only be that tool's,
    and its content must still name the tool.
    """
    generic = f"completion.{shell}"
    if shell == "bash":
        return (
            f"{tool}.bash",
            f"{tool}.bash-completion",
            f"{tool}-completion.bash",
            f"{tool}.bash_completion",
            generic,
        )
    if shell == "zsh":
        # `tool.zsh` is aichat's; its content must still register with compdef.
        return (f"_{tool}", f"{tool}.zsh", generic)
    return (f"{tool}.fish", generic)


def completion_paths(paths: Iterable[str], tool: str, shell: str) -> list[str]:
    """The relative paths, of those given, that may hold `tool`'s completion for
    `shell`: named as one, at most `MAX_DEPTH` deep, outside `_SKIPPED_DIRS`.
    A bare `tool` counts for bash only below a directory naming bash
    (`completions/bash/tool`, `bash-completion/completions/tool`)."""
    names = file_names(shell, tool)
    found = []
    for path in paths:
        parts = PurePosixPath(path).parts
        directories = [part.lower() for part in parts[:-1]]
        if len(parts) > MAX_DEPTH or _SKIPPED_DIRS.intersection(directories):
            continue
        name = parts[-1]
        bare = shell == "bash" and name == tool
        if name in names or (bare and any("bash" in d for d in directories)):
            found.append(path)
    return sorted(found)


@dataclass(frozen=True, slots=True)
class Ambiguous:
    """Several different completions for one shell: none is taken, since
    nothing says which is the tool's (CONTRACT.md rule 4)."""

    paths: tuple[str, ...]

    def __str__(self) -> str:
        return f"several different ones ({', '.join(self.paths)})"


def choose(candidates: list[tuple[str, str]]) -> tuple[str, str] | Ambiguous | None:
    """One `(path, script)` of those that read as a completion, if their
    contents agree; copies of one file are one completion."""
    by_content: dict[str, str] = {}
    for path, script in candidates:
        by_content.setdefault(script, path)
    if not by_content:
        return None
    if len(by_content) > 1:
        return Ambiguous(tuple(sorted(by_content.values())))
    [(script, path)] = by_content.items()
    return path, script


def shipped_completion(root: Path, tool: str, shell: str) -> Path | Ambiguous | None:
    """The completion for `shell` that `tool` installed in `root`, if any.

    Searched for by name anywhere in the root (bounded and pruned), as a
    user would with `find`. A file counts only when it resolves inside
    `root` -- the provider's own, as a shipped page must be -- and reads as a
    completion for that shell and tool; different ones are `Ambiguous`.
    """
    if not root.is_dir():
        return None
    candidates = []
    for relative in completion_paths(_files_below(root), tool, shell):
        candidate = root / relative
        try:
            candidate.resolve().relative_to(root.resolve())
            script = candidate.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        if looks_like_completion(script, shell, tool):
            candidates.append((relative, script))
    chosen = choose(candidates)
    if chosen is None or isinstance(chosen, Ambiguous):
        return chosen
    return root / chosen[0]


def _files_below(root: Path) -> Iterator[str]:
    """Every file's path relative to `root`, pruned of `_SKIPPED_DIRS` and of
    anything deeper than a completion could sit."""
    for directory, subdirectories, files in os.walk(root):
        relative = Path(directory).relative_to(root)
        depth = 0 if relative == Path(".") else len(relative.parts)
        subdirectories[:] = [
            d
            for d in subdirectories
            if d.lower() not in _SKIPPED_DIRS and depth + 1 < MAX_DEPTH
        ]
        for name in files:
            yield (relative / name).as_posix()


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
