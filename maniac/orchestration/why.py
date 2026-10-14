"""`why <tool>`: every decision maniac makes about one tool (CONTRACT.md rule 3).

Built from the same functions `install` and `list` use -- the login `$PATH`,
shim resolution, provider detection, `select_install_root`,
`select_repository`, the manifest -- so an explanation cannot disagree with
what those commands do.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from .. import manifest
from ..config import Config
from ..models import Installation, RepoSource
from ..sources.crawler import get_version
from ..sources.lookup.candidates import (
    Attempt,
    InstallRootCandidate,
    Repository,
    Source,
    attempts,
    repository_of,
    seed_for,
)
from ..sources.lookup.completions import completion_for, login_shell
from ..sources.lookup.resolution import Located, Outcome, locate, this_shell_runs
from ..sources.manpages import find_installed_manpage_path
from ..sources.pathcache import left_out_dirs, path_dirs
from ..sources.providers.base import Provider
from ..state.classification import classify
from ..state.models import Candidate
from ..state.pages import managed_pages
from .completions import subject, zsh_fpath_hint
from .context import redirect_lines, tool_from
from .profile_fix import login_profile_fix


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
    located = locate(tool)
    path = _path_section(tool, located)
    binary, claim, binary_path, refusal = _binary_section(tool, located, config)
    page = _page_section(tool, claim, config)
    sources, verdict = _sources_section(tool, claim, binary_path, refusal, config)
    if refusal is None:
        from .install import already_current

        # `install` checks this before any source (rule 3: `why` says what
        # `install` would do, not what it would do with `--force`).
        current = already_current(tool, config)
        if current is not None:
            verdict = f"{current.detail} ({verdict})"
    completions = _completions_section(tool, located, refusal, config)
    return Explanation(tool, [path, binary, page, sources, completions], verdict)


def _completions_section(
    tool: str, located: Located, refusal: str | None, config: Config
) -> Section:
    """For the login shell and each shell maniac installed one for: what is
    installed, and where `install` would take each completion from (rule 3)."""
    section = Section("Completions")
    recorded = manifest.completions_of(tool, config)
    login = login_shell()
    shells = tuple(dict.fromkeys([*([login] if login else []), *sorted(recorded)]))
    if login is None:
        name = Path(os.environ.get("SHELL") or "$SHELL").name
        section.lines.append(
            f"your login shell ({name}) is none maniac installs completions for; "
            "`install --shell bash|zsh|fish` names one"
        )
    if refusal is not None:
        section.lines.append(f"none installed: {refusal}")
        return section
    resolved = tool_from(located, config=config)
    for shell in shells:
        found = completion_for(subject(resolved), shell)
        entry = recorded.get(shell)
        if entry is not None:
            state = f"installed at {_home(entry.path)}"
        elif found.none_because is None:
            state = "install would add it"
        else:
            state = None
        line = f"{shell}: {found.describe(tool)}"
        section.lines.append(f"{line}; {state}" if state else line)
    if "zsh" in shells:
        hint = zsh_fpath_hint(config)
        if hint is not None:
            section.lines.append(hint)
    return section


def _path_section(tool: str, located: Located) -> Section:
    section = Section("PATH")
    login = path_dirs()
    shell = os.environ.get("SHELL", "$SHELL")
    section.lines.append(
        f"read from `{shell} -lc` started in $HOME: {len(login)} directories"
    )
    left_out = left_out_dirs()
    relevant = [d for d in left_out if (d / tool).exists()]
    for directory in relevant:
        section.lines.append(
            f"left out: {_home(directory)}, {_why_left_out(directory, located)}"
            " (holds a copy of this tool)"
        )
    others = len(left_out) - len(relevant)
    if others:
        section.lines.append(
            f"{others} more this shell has {'is' if others == 1 else 'are'} left out; "
            "none holds this tool"
        )
    return section


def _why_left_out(directory: Path, located: Located) -> str:
    """Why a directory this shell has is not on the login `$PATH`."""
    if (
        located.outcome is Outcome.OFF_LOGIN_PATH
        and located.hit is not None
        and located.hit.parent == directory
    ):
        return "an interactive rc file put it there, not your login profile"
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
    tool: str, located: Located, config: Config
) -> tuple[Section, tuple[Provider, Installation] | None, Path | None, str | None]:
    """The binary a page documents; the claim on it; and why not, if refused."""
    section = Section("Binary")
    here = this_shell_runs(tool)
    if located.outcome is Outcome.OFF_LOGIN_PATH:
        claim = located.claim
        assert claim is not None and located.binary is not None
        provider, inst = claim
        version = inst.version or "no version reported"
        section.lines.append("not on the login $PATH")
        section.lines.append(
            f"{provider.name} installed it globally as {inst.package} ({version}), "
            "but only an interactive rc file puts it on $PATH, and maniac reads "
            "the login profile"
        )
        section.lines.append(login_profile_fix(located))
        return _refused(section, located.binary, "not on the login $PATH")
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
    """Each source `install` tries, in its order, and the one it would use."""
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
        section.lines.append(_seed_line(tool, None, None, lambda: None, config))
        return section, "generated"

    provider, inst = claim
    repository: list[RepoSource | None] = []
    decided: list[Repository] = []

    def known_repository() -> RepoSource | None:
        found = repository_of(provider, inst, config)
        if found.error is not None:
            raise found.error
        if not decided:
            decided.append(found)
            section.lines.extend(redirect_lines(found, config))
            if found.unfinished and found.source is not None:
                section.lines.append(
                    f"repository: whether {found.source.identity} points at the "
                    "tool's own could not be checked; asked again next time"
                )
        repository.append(found.source)
        return found.source

    definitive = True
    for attempt in attempts(
        provider, inst, known_repository, config=config, cache_dir=config.cache_dir
    ):
        if attempt.source is Source.UPSTREAM and inst.version is None:
            # `install` asks nothing here; `why` still names the repository.
            attempt = replace(attempt, repository=known_repository())
        section.lines.append(_attempt_line(attempt, inst))
        if attempt.found is not None:
            return section, attempt.source.value
        definitive = definitive and attempt.definitive
    source = repository[0] if repository else None
    docs = f" and {source.identity}'s docs (`why --docs`)" if source is not None else ""
    section.lines.append(f"generated: from its own --help (`why --help-text`){docs}")
    section.lines.append(
        _seed_line(
            tool,
            provider,
            inst,
            lambda: repository[0] if repository else known_repository(),
            config,
        )
    )
    if not definitive:
        return section, "refused: the upstream check did not complete"
    return section, "generated"


def _seed_line(
    tool: str,
    provider: Provider | None,
    inst: Installation | None,
    repository: Callable[[], RepoSource | None],
    config: Config,
) -> str:
    """Which page people wrote for another version generation would start from."""
    seed = seed_for(
        tool, provider, inst, repository, config=config, cache_dir=config.cache_dir
    )
    if seed is None:
        return "seed: none found -- no page people wrote for another version of it"
    return f"seed: {seed.label}, {_home(seed.path)}"


def _attempt_line(attempt: Attempt, inst: Installation) -> str:
    """One source tried, and what it found or why it found nothing."""
    if attempt.source is Source.SHIPPED:
        if isinstance(attempt.found, InstallRootCandidate):
            return f"shipped: {_home(attempt.found.discovered_page)}"
        return f"shipped: none in {_home(inst.root)}"
    repository = attempt.repository
    if repository is None:
        return "upstream: no repository is known for it"
    if inst.version is None:
        return (
            f"upstream: {repository.identity}, but its installer reports no "
            "version to match a tag"
        )
    at = f"upstream: {repository.identity} at {inst.version}"
    if attempt.found is not None:
        return f"{at}: {attempt.found.primary.path.name}"
    if attempt.definitive:
        return f"{at}: no manpage"
    return f"{at}: the check failed (network?), so install refuses to generate"


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
