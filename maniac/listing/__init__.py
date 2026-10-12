"""`scan`'s inventory service: every tool on the login `$PATH`, and its page's state.

The states are `maniac.state`'s. The unit is a binary a provider detected (`resolution.locate_all`),
not a manpath scan, since that is what bounds what a bulk install could act on.
Each row's *state* is a reachability fact checked against `man` directly
(ADR-0018, reversing ADR-0013/ADR-0016's "the manpath is never scanned").

No module here renders, reads a console or touches Typer. Rich does appear in
`sys.modules` after importing this package, but only because `..logging` imports
structlog, which pulls it in itself. Callers observe a
run through `InventoryObserver`, which receives immutable ordered snapshots and
cannot reach the state the coordinator is still mutating.
"""

from .inventory import InventoryObserver, compute_rows, group_rows

__all__ = ["InventoryObserver", "compute_rows", "group_rows"]
