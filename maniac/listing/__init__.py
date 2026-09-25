"""`list`'s inventory service: what each binary's manpage reachability is.

| state      | means                                                                    |
|------------|--------------------------------------------------------------------------|
| ok         | a page resolves through `man` now and is current by local evidence       |
| unverified | an external page resolves but its matching package cannot be proven      |
| outdated   | a page resolves, and positive evidence says it documents another version |
| available  | nothing resolves, but a page can be had without an LLM                   |
| missing    | nothing resolves and no free page is known                               |
| error      | this tool's own metadata file is present but malformed (ADR-0060)        |

The unit is a binary a provider detected (`resolution.enumerate_installations`),
not a manpath scan, since that is what bounds what a bulk install could act on.
Each row's *state* is a reachability fact checked against `man` directly
(ADR-0018, reversing ADR-0013/ADR-0016's "the manpath is never scanned").

No module here renders, reads a console or touches Typer. Rich does appear in
`sys.modules` after importing this package, but only because `..logging` imports
structlog, which pulls it in itself. Callers observe a
run through `InventoryObserver`, which receives immutable ordered snapshots and
cannot reach the state the coordinator is still mutating.
"""

from .classification import classify
from .inventory import InventoryObserver, compute_rows, group_rows
from .models import (
    ActionState,
    Candidate,
    LocalClassification,
    PageSource,
    RowSnapshot,
    ToolRow,
)

__all__ = [
    "ActionState",
    "Candidate",
    "InventoryObserver",
    "LocalClassification",
    "PageSource",
    "RowSnapshot",
    "ToolRow",
    "classify",
    "compute_rows",
    "group_rows",
]
