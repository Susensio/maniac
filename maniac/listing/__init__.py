"""`list`'s inventory service: what each binary's manpage reachability is.

| state      | means                                                                    |
|------------|--------------------------------------------------------------------------|
| ok         | a page resolves through `man` now and is current by local evidence       |
| unverified | an external page resolves but its matching package cannot be proven      |
| outdated   | a page resolves, and positive evidence says it documents another version |
| available  | nothing resolves, but a page can be had without an LLM                   |
| missing    | nothing resolves and no free page is known                               |

The unit is a binary a provider detected (`resolution.enumerate_installations`),
not a manpath scan, since that is what bounds what a bulk install could act on.
Each row's *state* is a reachability fact checked against `man` directly
(ADR-0018, reversing ADR-0013/ADR-0016's "the manpath is never scanned").

Nothing under this package imports Rich, Typer or a console. Callers observe a
run through `InventoryObserver`, which receives immutable ordered snapshots and
cannot reach the state the coordinator is still mutating.
"""

from .classification import classify
from .inventory import InventoryObserver, compute_rows
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
]
