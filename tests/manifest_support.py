"""Single-mutation manifest writes, for tests that need a manifest to exist.

Production code writes through `manifest.transaction`, one transaction per
operation.  A test building a fixture has no operation to hang one off, so
these wrap a single put or forget in their own transaction.
"""

from maniac import manifest
from maniac.config import Config
from maniac.manifest import Entry


def record_entry(tool: str, entry: Entry, config: Config | None = None) -> None:
    """Record one tool's entry, replacing any it already had."""
    with manifest.transaction(config) as txn:
        txn.put(tool, entry)


def forget_entry(tool: str, config: Config | None = None) -> None:
    """Drop one tool's entry, if it has one."""
    with manifest.transaction(config) as txn:
        txn.forget(tool)
