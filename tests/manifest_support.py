"""Single-mutation manifest writes, for tests that need a manifest to exist.

Production code writes through `manifest.transaction`, one transaction per
operation.  A test building a fixture has no operation to hang one off, so
these wrap a single put or forget in their own transaction.
"""

from pathlib import Path

from maniac import manifest
from maniac.config import Config
from maniac.manifest import Entry, Tier


def record_entry(
    tool: str,
    path: Path,
    tier: Tier,
    source: str,
    checksum: str,
    backup: Path | None = None,
    config: Config | None = None,
    *,
    version: str | None = None,
    source_uri: str | None = None,
    target: Path | None = None,
    provider_target: bool = False,
    group: str | None = None,
) -> None:
    """Record one tool's entry, replacing any it already had."""
    with manifest.transaction(config) as txn:
        txn.put(
            tool,
            Entry(
                path=Path(path),
                tier=tier,
                source=source,
                checksum=checksum,
                backup=backup,
                version=version,
                source_uri=source_uri,
                target=target,
                provider_target=provider_target,
                group=group,
            ),
        )


def forget_entry(tool: str, config: Config | None = None) -> None:
    """Drop one tool's entry, if it has one."""
    with manifest.transaction(config) as txn:
        txn.forget(tool)
