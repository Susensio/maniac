"""The pages maniac installed, each against the tool it documents (CONTRACT.md).

`list` and `update` read this, not `$PATH` discovery (that is `scan`): every
row is a manifest entry standing for a tool -- a release's primary page or a
page that arrived alone -- so ownership is never a question here.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .. import manifest
from ..config import Config
from .classification import managed_page, managed_source
from .inventory import named_candidate
from .models import ActionState, PageSource


@dataclass(frozen=True, slots=True)
class PageRow:
    """One page maniac installed: what it documents, what is installed, its state.

    `documented` and `installed` are first lines, for display; `state`
    compares the full strings (an unclaimed binary's `--version` is recorded
    verbatim, ADR-0020). `copy` is the binary a pinned page documents
    (`install --force`, CONTRACT.md rule 2), None for every other page.
    `note` says why a page is `unknown`, or what is wrong with its link.
    """

    tool: str
    state: ActionState
    source: PageSource
    documented: str | None
    installed: str | None
    copy: Path | None = None
    drift: bool = False
    note: str | None = None


def managed_pages(
    config: Config, tools: Iterable[str] | None = None
) -> tuple[list[PageRow], list[str]]:
    """Each managed page standing for a tool, and the named tools with none.

    With no names, every page maniac installed; with names, exactly those,
    and the names maniac manages no page for come back separately.
    """
    entries = manifest.load(config)
    primaries = {
        tool: entry for tool, entry in entries.items() if entry.group in (None, tool)
    }
    if tools is None:
        wanted = sorted(primaries)
        unmanaged: list[str] = []
    else:
        named = list(dict.fromkeys(tools))
        wanted = [tool for tool in named if tool in primaries]
        unmanaged = [tool for tool in named if tool not in primaries]
    return [_page_row(tool, primaries[tool], config) for tool in wanted], unmanaged


def _page_row(tool: str, entry: manifest.Entry, config: Config) -> PageRow:
    judged = managed_page(named_candidate(tool), entry, config)
    return PageRow(
        tool=tool,
        state=judged.state,
        source=managed_source(entry),
        documented=_first_line(entry.version),
        installed=_first_line(judged.installed),
        copy=entry.binary,
        drift=judged.drift,
        note=judged.note,
    )


def _first_line(text: str | None) -> str | None:
    if text is None:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[0] if lines else None
