"""A tool's shell completions, for `install`, `update`, `remove` and `why` (ADR-0066).

Where each shell's completion comes from is `sources.lookup.completions`'s
to answer (`completion_for`, given a `subject`); `install` links what it
finds (`install_completions`), and `remove` unlinks what maniac recorded.

maniac never edits a shell's startup files (CONTRACT.md rule 1): zsh reads
completions only from `$fpath`, so when nothing in the user's zsh startup
files names maniac's directory, `zsh_fpath_hint` says which line to add.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from .. import manifest
from ..completion_files import (
    CompletionTaken,
    install_completion,
    remove_completions,
)
from ..config import Config
from ..sources.lookup.completions import Found, Help, Subject, completion_for
from .context import ResolvedTool


def subject(tool: ResolvedTool) -> Subject:
    """What the completion lookup needs to know about a resolved tool."""
    inst = tool.installation
    return Subject(
        name=inst.binary if inst is not None else tool.tool_name,
        executable=tool.executable,
        config=tool.config,
        cache_dir=tool.cache_dir,
        bin_path=tool.bin_path,
        installation=inst,
        provider=tool.provider,
        page=_installed_page(tool),
    )


def _installed_page(tool: ResolvedTool) -> Path | None:
    """The page maniac installed for `tool`, which fish's parser reads."""
    entry = manifest.lookup(tool.tool_name, tool.config)
    if entry is None:
        return None
    page = manifest.expected_target_path(entry)
    return page if page.is_file() else None


@dataclass(frozen=True, slots=True)
class CompletionOutcome:
    """What `install` did, or would do, about one shell."""

    shell: str
    detail: str
    installed: bool


def install_completions(
    tool: ResolvedTool, shells: tuple[str, ...], *, dry_run: bool
) -> tuple[CompletionOutcome, ...]:
    """Find and link `tool`'s completion for each shell, in one transaction."""
    if not shells:
        return ()
    looked_up = subject(tool)
    help_ = Help(looked_up)
    found = [completion_for(looked_up, shell, help_) for shell in shells]
    name = looked_up.name
    outcomes = []
    if dry_run:
        return tuple(
            CompletionOutcome(f.shell, f.describe(name), f.none_because is None)
            for f in found
        )
    with manifest.transaction(tool.config) as txn:
        for f in found:
            if f.none_because is not None:
                outcomes.append(CompletionOutcome(f.shell, f.describe(name), False))
                continue
            try:
                install_completion(
                    name,
                    f.shell,
                    shipped=f.shipped,
                    script=_script_of(f),
                    source=_source_of(f),
                    arguments=f.generated.arguments if f.generated else (),
                    version=tool.installed_version,
                    txn=txn,
                )
            except CompletionTaken as taken:
                outcomes.append(
                    CompletionOutcome(
                        f.shell,
                        "not installed: a completion maniac did not install is "
                        f"there, {_home(taken.path)}",
                        False,
                    )
                )
                continue
            outcomes.append(CompletionOutcome(f.shell, f.describe(name), True))
    return tuple(outcomes)


def _script_of(found: Found) -> str | None:
    if found.upstream is not None:
        return found.upstream.script
    if found.derived is not None:
        return found.derived.script
    return found.generated.script if found.generated is not None else None


def _source_of(found: Found) -> str:
    if found.upstream is not None:
        return "upstream"
    return "derived" if found.derived is not None else "generated"


def recorded_shells(tool: str, config: Config) -> tuple[str, ...]:
    """The shells maniac installed `tool`'s completions for (`update` keeps to them)."""
    return tuple(sorted(manifest.completions_of(tool, config)))


def remove_tool_completions(tool: str, config: Config) -> tuple[str, ...]:
    """Remove `tool`'s completions; the shells they were removed for."""
    with manifest.transaction(config) as txn:
        return remove_completions(tool, txn).removed


def zsh_fpath_hint(config: Config) -> str | None:
    """The line to add when no zsh startup file names maniac's directory."""
    directory = config.completion_dirs["zsh"]
    home = Path(os.environ.get("ZDOTDIR") or Path.home())
    spellings = {str(directory), _home(directory), _xdg_spelling(directory)}
    for name in (".zshrc", ".zshenv", ".zprofile"):
        try:
            text = (home / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(spelling in text for spelling in spellings if spelling):
            return None
    return (
        f"zsh reads completions only from $fpath: add `fpath=({_home(directory)} "
        "$fpath)` to ~/.zshrc, before `compinit`"
    )


def _xdg_spelling(directory: Path) -> str:
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    try:
        return f"$XDG_DATA_HOME/{directory.relative_to(data)}"
    except ValueError:
        return ""


def _home(path: Path) -> str:
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)
