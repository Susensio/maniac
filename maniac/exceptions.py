"""Exception hierarchy for the maniac package."""

from pathlib import Path


class ManiacError(Exception):
    """Base exception for all maniac errors."""


class CrawlerError(ManiacError):
    """Raised when crawling CLI help fails."""


class GenerationError(ManiacError):
    """Raised when generating manpages via LLM or compiler fails."""


class MalformedToolMetadata(ManiacError):
    """A tool's own metadata file is present but cannot be read (ADR-0060).

    Raised at the point a malformed file is read, never swallowed into a
    guess or a silent skip. A missing file is a distinct, ordinary case and
    never raises this.
    """

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


class BrokenLoginShell(ManiacError):
    """The login shell could not report `$PATH` (ADR-0060, ADR-0062).

    maniac resolves every binary through `$SHELL -lc 'printenv PATH'` at
    `$HOME`; when that fails, nothing can be resolved, so it stops with the
    reason instead of guessing at a `$PATH`. `shell` is None when `$SHELL`
    itself is unset.
    """

    def __init__(self, shell: str | None, reason: str) -> None:
        self.shell = shell
        self.reason = reason
        who = f"login shell {shell}" if shell else "login shell"
        super().__init__(f"{who}: {reason}")


class NotGloballySelected(ManiacError):
    """A Mise installation exists but is not among Mise's globally selected
    tools (`mise ls --current` from $HOME, ADR-0061).

    Covers more than a project-scoped install: an orphaned install nothing
    on this machine selects, a stale version left first on `$PATH` after an
    upgrade, or a hand-added `$PATH` entry all land here too -- "resolves,
    but not globally selected" is the one fact this exception states.

    Refused outright rather than treated as unclaimed -- an unclaimed
    binary falls to tier-3 synthesis, which would document this resolved
    version as if it were the machine's global one.
    """

    def __init__(self, tool: str, root: Path) -> None:
        self.tool = tool
        self.path = root
        # Short: `listing.py` renders this in the Source column, capped at
        # `_SOURCE_COLUMN_MAX_WIDTH` -- the fuller explanation belongs to
        # the caller's own message (`orchestration/install.py`'s refusal).
        self.reason = "not a globally selected mise tool"
        super().__init__(f"{tool}: {self.reason} ({root})")


class ShimRunsNothing(NotGloballySelected):
    """A Mise shim that runs nothing from `$HOME` (ADR-0063 Corrections).

    No globally active tool provides it and no other binary of that name
    is on the login `$PATH` for mise to fall through to -- typically the
    shim of a project-only tool. Named, it is refused like any project-only
    install; `list` with no names leaves it out, since from `$HOME` the name
    reaches no binary at all.
    """

    def __init__(self, tool: str, shim: Path) -> None:
        super().__init__(tool, shim)
