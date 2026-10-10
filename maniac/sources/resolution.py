"""Resolve PATH binaries through the ordered installer-provider registry.

The one place that knows both how a binary is named and which providers
exist, and the one place commands ask: `locate` (one tool), `locate_file` (a
pinned copy), `locate_all` (the login `$PATH`) and `this_shell_runs`. Each
answers with a `Located` value; no resolution exception escapes them.
Providers and `discovery.py` sit below it and import nothing from here, so
composition is carried by the registry passed down a call rather than by
module- or class-global state.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from ..exceptions import MalformedToolMetadata, NotGloballySelected, ShimRunsNothing
from ..models import Installation
from .pathcache import path_dirs, resolve_bin_path, which_here
from .providers.base import Provider
from .providers.mise import is_shim, shim_target, shim_target_here
from .providers.registry import registry


def binary_path(binary_name: str, bin_dir: str | Path | None = None) -> Path | None:
    """The file that actually runs for `binary_name` from `$HOME`, or None.

    `resolve_bin_path`'s answer, with a Mise shim replaced by what it
    dispatches to (ADR-0063) -- a shim picks its version by working
    directory, so running it from maniac's own would answer for whichever
    project maniac happens to be in. Every caller that runs or detects a
    binary by name goes through this, claimed or not. Raises what
    `shim_target` raises.
    """
    found = resolve_bin_path(binary_name, bin_dir)
    if found is None:
        return None
    return shim_target(found) or found


def is_system_binary(bin_path: Path) -> bool:
    """Whether `bin_path` is a system package's binary, out of scope (ADR-0059)."""
    return registry.is_system_path(bin_path)


def find_installation(
    binary_name: str, bin_dir: str | Path | None = None, *, here: bool = False
) -> tuple[Provider, Installation] | None:
    """Return the provider and `Installation` a binary resolves to, if any.

    Resolves the bin path the same way every caller does, but stops at the
    `Installation` itself rather than resolving its source -- ADR-0016's
    tier 1 (install root) and tier 2 (repository, version matched) both
    need the install root and version directly, not only what
    `resolve_source` derives from them.
    """
    bin_path = binary_path(binary_name, bin_dir)
    return _detect_via_registry(bin_path, here=here) if bin_path is not None else None


class Outcome(Enum):
    """What looking a tool up on the login `$PATH` found (CONTRACT.md rule 2)."""

    FOUND = "found"
    """A binary runs for it from `$HOME`; an installer may or may not claim it."""
    NOT_ON_PATH = "not on path"
    SHIM_RUNS_NOTHING = "shim runs nothing"
    """A mise shim no globally selected tool provides (ADR-0063 Corrections)."""
    NOT_GLOBAL = "not global"
    """A mise install no global config selects (ADR-0061)."""
    UNREADABLE = "unreadable"
    """Its installer's metadata, or mise behind a shim, could not be read (ADR-0060)."""


@dataclass(frozen=True, slots=True)
class Located:
    """One tool's lookup: the one answer every command maps to what it prints.

    `hit` is what the login `$PATH` (or an explicit directory) reaches, a
    mise shim itself if that is what sits there; `binary` is what actually
    runs for it from `$HOME`. `error` carries the refusal for the outcomes
    that are not `FOUND`, with its path and reason.
    """

    tool: str
    outcome: Outcome
    hit: Path | None = None
    binary: Path | None = None
    provider: Provider | None = None
    installation: Installation | None = None
    error: MalformedToolMetadata | NotGloballySelected | None = None

    @property
    def claim(self) -> tuple[Provider, Installation] | None:
        if self.provider is None or self.installation is None:
            return None
        return self.provider, self.installation

    @property
    def via_shim(self) -> bool:
        return self.hit is not None and is_shim(self.hit)

    @property
    def is_system(self) -> bool:
        """An unclaimed binary a system package owns (ADR-0059)."""
        return (
            self.outcome is Outcome.FOUND
            and self.installation is None
            and self.binary is not None
            and is_system_binary(self.binary)
        )


def locate(
    tool: str, bin_dir: str | Path | None = None, *, accept_project: bool = False
) -> Located:
    """Look `tool` up as the login shell runs it from `$HOME`; never raises.

    `bin_dir` looks in that directory first. `accept_project` answers with
    an install mise would refuse as not globally selected: `install --force`
    asked for that very copy (CONTRACT.md rule 2).
    """
    hit = resolve_bin_path(tool, bin_dir)
    try:
        claim = find_installation(tool, bin_dir=bin_dir, here=accept_project)
        binary = claim[1].bin_path if claim is not None else binary_path(tool, bin_dir)
    except ShimRunsNothing as e:
        return Located(tool, Outcome.SHIM_RUNS_NOTHING, hit, error=e)
    except NotGloballySelected as e:
        return Located(
            tool,
            Outcome.NOT_GLOBAL,
            hit,
            binary=_reached(hit),
            installation=e.installation,
            error=e,
        )
    except MalformedToolMetadata as e:
        return Located(tool, Outcome.UNREADABLE, hit, binary=_reached(hit), error=e)
    if claim is None and binary is None:
        return Located(tool, Outcome.NOT_ON_PATH)
    provider, inst = claim if claim is not None else (None, None)
    return Located(tool, Outcome.FOUND, hit, binary, provider, inst)


def locate_file(bin_path: Path) -> Located:
    """Who installed the file at `bin_path`, global or not.

    For a page that documents a copy `install --force` chose (CONTRACT.md
    rule 2): it is checked on that copy, project-scoped or not.
    """
    try:
        claim = _detect_via_registry(bin_path, here=True)
    except (MalformedToolMetadata, NotGloballySelected) as e:
        return Located(bin_path.name, Outcome.UNREADABLE, bin_path, bin_path, error=e)
    provider, inst = claim if claim is not None else (None, None)
    return Located(bin_path.name, Outcome.FOUND, bin_path, bin_path, provider, inst)


def this_shell_runs(tool: str) -> Path | None:
    """The file the invoking shell runs for `tool`, or None.

    The inherited `$PATH`'s first hit, a mise shim followed as mise would
    from the current directory. Compared with `Located.binary`, it tells when
    the shell maniac was run from would run another copy than the one a page
    documents (CONTRACT.md rule 2).
    """
    found = which_here(tool)
    if found is not None and is_shim(found):
        return shim_target_here(found) or found
    return found


def _reached(hit: Path | None) -> Path | None:
    """What runs for `hit`, when that is all that can be said: its shim target,
    or None when the shim itself could not be followed."""
    if hit is None or not is_shim(hit):
        return hit
    try:
        return shim_target(hit) or hit
    except (MalformedToolMetadata, NotGloballySelected):
        return None


def locate_all(
    on_start: Callable[[int], None] | None = None,
    on_scan: Callable[[], None] | None = None,
) -> list[Located]:
    """Look up every name on the login `$PATH`, once each, at its first hit.

    The binary that runs is the first one on `$PATH` (ADR-0016's
    tie-break), so a later hit never wins; an installer that claims a later
    one too is kept on the winner's `Installation.losers`, so `why` can say
    when `$PATH` hides a better-documented install.

    `on_start` fires once with the number of names, after the directory
    walk; `on_scan` once per name looked up.
    """
    first, shadowed = _path_names()
    if on_start is not None:
        on_start(len(first))
    located = []
    for name, bin_path in first.items():
        found = _locate_hit(name, bin_path)
        if found.installation is not None and shadowed.get(name):
            found = _with_losers(found, shadowed[name])
        located.append(found)
        if on_scan is not None:
            on_scan()
    return located


def _path_names() -> tuple[dict[str, Path], dict[str, list[Path]]]:
    """Every executable name on the login `$PATH`: its first hit, and the rest."""
    first: dict[str, Path] = {}
    shadowed: dict[str, list[Path]] = {}
    for entry in path_dirs():
        try:
            children = list(os.scandir(entry))
        except OSError:
            continue
        for child in children:
            try:
                if not child.is_file() or not os.access(child.path, os.X_OK):
                    continue
            except OSError:
                continue
            if child.name in first:
                shadowed.setdefault(child.name, []).append(Path(child.path))
            else:
                first[child.name] = Path(child.path)
    return first, shadowed


def _locate_hit(name: str, hit: Path) -> Located:
    """`locate` for a name whose `$PATH` hit is already known."""
    try:
        claim = _detect_via_registry(hit)
    except ShimRunsNothing as e:
        return Located(name, Outcome.SHIM_RUNS_NOTHING, hit, error=e)
    except NotGloballySelected as e:
        return Located(
            name,
            Outcome.NOT_GLOBAL,
            hit,
            binary=_reached(hit),
            installation=e.installation,
            error=e,
        )
    except MalformedToolMetadata as e:
        return Located(name, Outcome.UNREADABLE, hit, binary=_reached(hit), error=e)
    if claim is None:
        return Located(name, Outcome.FOUND, hit, _reached(hit))
    provider, inst = claim
    return Located(name, Outcome.FOUND, hit, inst.bin_path, provider, inst)


def _with_losers(found: Located, shadows: list[Path]) -> Located:
    """`found`, its installation listing who also claims a later `$PATH` hit."""
    assert found.installation is not None
    losers = []
    for shadow in shadows:
        if shadow == found.installation.bin_path:
            # The binary a shim fell through to is the winner itself.
            continue
        try:
            claim = _detect_via_registry(shadow)
        except (MalformedToolMetadata, NotGloballySelected):
            continue
        if claim is not None:
            losers.append(claim[1])
    if not losers:
        return found
    return replace(
        found, installation=replace(found.installation, losers=tuple(losers))
    )


def _detect_via_registry(
    bin_path: Path, *, here: bool = False
) -> tuple[Provider, Installation] | None:
    """Return the first registered provider that claims one PATH candidate.

    A Mise shim is replaced by the binary it dispatches to from `$HOME`
    first (ADR-0063): the shim itself is the `mise` executable, which no
    provider owns, and what it runs depends on the directory it runs in.

    `here` accepts an install its provider found but would refuse as not
    globally selected -- `install --force` documents that copy on purpose.
    """
    target = shim_target(bin_path)
    if target is not None:
        bin_path = target
    for provider in registry.candidates_for(bin_path):
        try:
            inst = provider.detect(bin_path)
        except NotGloballySelected as e:
            # `here`: the caller asked for this very copy (`install --force`,
            # CONTRACT.md rule 2), so a project-scoped install is its answer.
            if here and e.installation is not None:
                return provider, e.installation
            raise
        if inst is not None:
            return provider, inst
    return None
