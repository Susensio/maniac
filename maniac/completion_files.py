"""Install and remove a tool's shell completions (ADR-0066).

Each completion is a symbolic link in the shell's user directory
(`Config.completion_dirs`): to the provider's own file for a shipped one,
so an upgrade in place refreshes it, or to maniac's copy of what the tool
generated (`Config.completions_output_dir`). Every link is recorded in the
manifest's `completions` section, and only a recorded link is ever replaced
or removed: a completion maniac did not install is reported, never touched
(CONTRACT.md rule 1).
"""

from dataclasses import dataclass
from pathlib import Path

from . import manifest
from .config import Config


class CompletionTaken(Exception):
    """A completion maniac did not install already sits where it would link."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"a completion maniac did not install is there: {path}")


def link_path(shell: str, tool: str, config: Config) -> Path:
    return config.completion_dirs[shell] / manifest.completion_link_name(shell, tool)


def install_completion(
    tool: str,
    shell: str,
    *,
    shipped: Path | None = None,
    script: str | None = None,
    source: str = "generated",
    arguments: tuple[str, ...] = (),
    version: str | None,
    txn: manifest.Transaction,
) -> manifest.CompletionEntry:
    """Link `tool`'s completion for `shell` and record it in `txn`.

    Either `shipped` (the provider's file, already proven inside its install
    root) is linked, or `script` -- from `source`, "upstream" or
    "generated" (with the generator's `arguments`) -- is saved as maniac's
    copy and linked. Raises `CompletionTaken` when something maniac did not
    record sits at the link's path.
    """
    assert (shipped is None) != (script is None)
    cfg = txn.config
    link = link_path(shell, tool, cfg)
    recorded = txn.completions.get(tool, {}).get(shell)
    if (link.exists() or link.is_symlink()) and (
        recorded is None or recorded.path != link
    ):
        raise CompletionTaken(link)

    if shipped is not None:
        target, source, arguments = shipped, "shipped", ()
    else:
        assert script is not None
        target = (
            cfg.completions_output_dir
            / shell
            / manifest.completion_link_name(shell, tool)
        )
        _write_atomically(target, script)
    _link_atomically(link, target)

    entry = manifest.CompletionEntry(
        path=link,
        target=target,
        source=source,
        checksum=manifest.checksum_of(target),
        version=version,
        provider_target=shipped is not None,
        arguments=arguments,
    )
    if recorded is not None and recorded.target != target:
        _remove_own_copy(recorded, cfg)
    txn.put_completion(tool, shell, entry)
    return entry


@dataclass(frozen=True, slots=True)
class Removed:
    """What removing one tool's completions did, shell by shell."""

    removed: tuple[str, ...]
    kept: tuple[Path, ...]
    """Links someone repointed: no longer provably maniac's, left in place."""


def remove_completions(tool: str, txn: manifest.Transaction) -> Removed:
    """Unlink and forget every completion recorded for `tool`, deleting
    maniac's own copies; never a provider's file, nor a link someone
    repointed (it is forgotten and reported instead)."""
    removed, kept = [], []
    for shell, entry in sorted(txn.completions.get(tool, {}).items()):
        if manifest.completion_link_holds(entry):
            entry.path.unlink()
            removed.append(shell)
        elif entry.path.exists() or entry.path.is_symlink():
            kept.append(entry.path)
        _remove_own_copy(entry, txn.config)
        txn.forget_completion(tool, shell)
    return Removed(tuple(removed), tuple(kept))


def _remove_own_copy(entry: manifest.CompletionEntry, config: Config) -> None:
    """Delete a generated script maniac wrote; a provider's file is never ours."""
    if entry.provider_target:
        return
    try:
        entry.target.relative_to(config.completions_output_dir)
    except ValueError:
        return
    entry.target.unlink(missing_ok=True)


def _write_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _link_atomically(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".{link.name}.tmp")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    temporary.replace(link)
