"""`why <tool>`: every decision maniac makes about one tool (CONTRACT.md rule 3).

Built from the same functions `install` and `list` use -- the login `$PATH`,
shim resolution, provider detection, `select_install_root`,
`select_repository`, the manifest -- so an explanation cannot disagree with
what those commands do.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from .. import manifest
from ..config import Config
from ..listing.classification import classify
from ..listing.models import Candidate
from ..listing.pages import managed_pages
from ..models import Installation
from ..sources import documentation
from ..sources.candidates import select_install_root, select_repository
from ..sources.crawler import get_version
from ..sources.docs import discover_repo_manpages
from ..sources.manpages import find_installed_manpage_path
from ..sources.pathcache import path_dirs
from ..sources.providers.base import Provider
from ..sources.providers.registry import registry
from ..sources.resolution import Outcome, locate, this_shell_runs


@dataclass
class Section:
    title: str
    lines: list[str] = field(default_factory=list)


@dataclass
class Explanation:
    """The sections `why` prints, and the verdict `install` would reach."""

    tool: str
    sections: list[Section]
    verdict: str


def explain(tool: str, config: Config) -> Explanation:
    """Every decision about `tool`, in the order maniac makes them."""
    path = _path_section(tool)
    binary, claim, binary_path, refusal = _binary_section(tool, config)
    page = _page_section(tool, claim, config)
    sources, verdict = _sources_section(tool, claim, binary_path, refusal, config)
    if refusal is None:
        from .install import already_current

        # `install` checks this before any source (rule 3: `why` says what
        # `install` would do, not what it would do with `--force`).
        current = already_current(tool, config)
        if current is not None:
            verdict = f"{current.detail} ({verdict})"
    return Explanation(tool, [path, binary, page, sources], verdict)


def _path_section(tool: str) -> Section:
    section = Section("PATH")
    login = path_dirs()
    shell = os.environ.get("SHELL", "$SHELL")
    section.lines.append(
        f"read from `{shell} -lc` started in $HOME: {len(login)} directories"
    )
    inherited = [
        Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry
    ]
    left_out = [d for d in dict.fromkeys(inherited) if d not in set(login)]
    relevant = [d for d in left_out if (d / tool).exists()]
    for directory in relevant:
        section.lines.append(
            f"left out: {_home(directory)}, {_why_left_out(directory)}"
            " (holds a copy of this tool)"
        )
    others = len(left_out) - len(relevant)
    if others:
        section.lines.append(
            f"{others} more this shell has {'is' if others == 1 else 'are'} left out; "
            "none holds this tool"
        )
    return section


def _why_left_out(directory: Path) -> str:
    """Why a directory this shell has is not on the login `$PATH`."""
    for variable, what in (
        ("VIRTUAL_ENV", "an activated virtualenv"),
        ("CONDA_PREFIX", "a conda environment"),
    ):
        root = os.environ.get(variable)
        if root and directory.is_relative_to(Path(root)):
            return f"{what} ({variable})"
    if "/mise/installs/" in str(directory):
        return "mise activated it for this directory"
    return "your login profile does not put it on $PATH"


def _binary_section(
    tool: str, config: Config
) -> tuple[Section, tuple[Provider, Installation] | None, Path | None, str | None]:
    """The binary a page documents; the claim on it; and why not, if refused."""
    section = Section("Binary")
    here = this_shell_runs(tool)
    located = locate(tool)
    if located.hit is None:
        section.lines.append("not on the login $PATH")
        return _refused(section, here, "not installed globally")

    section.lines.append(f"login $PATH reaches {_home(located.hit)}")
    target = located.binary
    if located.outcome is Outcome.SHIM_RUNS_NOTHING:
        section.lines.append(
            "a mise shim that runs nothing from $HOME: no globally selected "
            "tool provides it"
        )
        return _refused(section, here, "not installed globally")
    if located.via_shim:
        if target is None:
            assert located.error is not None
            section.lines.append(
                f"a mise shim, and mise failed: {located.error.reason}"
            )
            return section, None, None, located.error.reason
        section.lines.append(f"a mise shim; from $HOME it runs {_home(target)}")

    if located.outcome is Outcome.NOT_GLOBAL:
        assert located.error is not None
        section.lines.append(
            f"{_home(located.error.path)} is a mise install no global config selects"
        )
        return _refused(section, here, "not installed globally")
    if located.outcome is Outcome.UNREADABLE:
        assert located.error is not None
        section.lines.append(
            "its installer's metadata is unreadable: "
            f"{located.error.path}: {located.error.reason}"
        )
        return section, None, target, located.error.reason

    assert target is not None
    claim = located.claim
    if claim is not None:
        provider, inst = claim
        version = inst.version or "no version reported"
        section.lines.append(
            f"installed by {provider.name} as {inst.package} ({version}), "
            f"root {_home(inst.root)}"
        )
    else:
        reported = get_version([str(target)], config=config)
        first = reported.splitlines()[0].strip() if reported else None
        section.lines.append(
            "no installer claims it; "
            + (f"it reports {first}" if first else "it reports no version")
        )
        if located.is_system:
            section.lines.append(
                "a system package's binary: maniac leaves it to its package; "
                "`--force` installs anyway"
            )
            if here is not None and _differs(here, target):
                section.lines.append(f"this shell runs {_home(here)} instead")
            return section, None, target, "a system package's binary"
    if here is not None and _differs(here, target):
        section.lines.append(f"this shell runs {_home(here)} instead")
    return section, claim, target, None


def _refused(
    section: Section, here: Path | None, reason: str
) -> tuple[Section, None, None, str]:
    if here is not None:
        section.lines.append(
            f"this shell runs {_home(here)}; `--force` documents that copy"
        )
    return section, None, None, reason


def _page_section(
    tool: str, claim: tuple[Provider, Installation] | None, config: Config
) -> Section:
    section = Section("Page")
    rows, _ = managed_pages(config, [tool])
    if rows:
        [row] = rows
        section.lines.append(
            f"maniac's ({row.source.value}): documents {row.documented or 'no recorded version'}"
            f", installed {row.installed or 'unknown'}: {row.state.value}"
        )
        if row.copy is not None:
            section.lines.append(f"pinned to {_home(row.copy)} by --force")
        if row.note is not None:
            section.lines.append(row.note)
        return section
    if manifest.lookup(tool, config=config) is not None:
        section.lines.append("a companion page; it answers to its release's main page")
        return section
    shown = find_installed_manpage_path("man", tool)
    if shown is None:
        section.lines.append("`man` finds no page for it")
        return section
    provider, inst = claim if claim is not None else (None, None)
    local = classify(Candidate(tool=tool, provider=provider, installation=inst), config)
    owner = f" (package {local.owning_package})" if local.owning_package else ""
    section.lines.append(
        f"`man` shows {_home(shown)}, not maniac's: {local.source.value or 'unknown source'}"
        f"{owner}, {local.state.value}"
    )
    return section


def _sources_section(
    tool: str,
    claim: tuple[Provider, Installation] | None,
    binary: Path | None,
    refusal: str | None,
    config: Config,
) -> tuple[Section, str]:
    """Each source `install` tries, in order, and the one it would use."""
    section = Section("Sources")
    if refusal is not None:
        section.lines.append(f"none tried: {refusal}")
        return section, f"refused: {refusal}"
    if claim is None:
        section.lines.append("shipped: no installer, so no install root to look in")
        section.lines.append(
            "upstream: no installer, so no repository or version known"
        )
        section.lines.append(
            "generated: from its own --help (`why --help-text` shows it)"
        )
        return section, "generated"

    provider, inst = claim
    shipped = select_install_root(provider, inst)
    if shipped is not None:
        section.lines.append(f"shipped: {_home(shipped.discovered_page)}")
        return section, "shipped"
    section.lines.append(f"shipped: none in {_home(inst.root)}")

    raw = registry.resolve_source(inst, config=config, provider=provider)
    source = (
        documentation.documentation_source(
            raw, config.documentation_repository_overrides
        )
        if raw is not None
        else None
    )
    definitive = True
    if source is None:
        section.lines.append("upstream: no repository is known for it")
    elif inst.version is None:
        section.lines.append(
            f"upstream: {source.identity}, but its installer reports no version to match a tag"
        )
    else:
        candidate, definitive = select_repository(
            source,
            inst.binary,
            cache_dir=config.cache_dir,
            config=config,
            version=inst.version,
            discover=discover_repo_manpages,
        )
        if candidate is not None:
            section.lines.append(
                f"upstream: {source.identity} at {inst.version}: {candidate.primary.path.name}"
            )
            return section, "upstream"
        section.lines.append(
            f"upstream: {source.identity} at {inst.version}: "
            + (
                "no manpage"
                if definitive
                else "the check failed (network?), so install refuses to generate"
            )
        )
    docs = f" and {source.identity}'s docs (`why --docs`)" if source is not None else ""
    section.lines.append(f"generated: from its own --help (`why --help-text`){docs}")
    if not definitive:
        return section, "refused: the upstream check did not complete"
    return section, "generated"


def _differs(a: Path, b: Path) -> bool:
    try:
        return a.resolve() != b.resolve()
    except OSError:
        return a != b


def _home(path: Path) -> str:
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)
