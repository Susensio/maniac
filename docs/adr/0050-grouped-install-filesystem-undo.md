# ADR-0050: Undo a grouped install's earlier pages on a later page's failure, instead of healing checksums after the fact

Status: Superseded by [ADR-0051](0051-grouped-install-undo-restore-fix.md)
Date: 2026-09-21

## Context

ADR-0046 made a multi-page release's manifest write atomic: `orchestration.install._try_repository` joins one `manifest.transaction` around the whole per-page loop, so every page's entry lands in one write or none.
Its Postscript already found and named the gap that write alone leaves open, live, before this record: a grouped *reinstall* failing partway through the loop leaves the manifest correctly unchanged (the transaction discards every mutation, exactly as designed), but the filesystem does not roll back with it.
Materialization happens per page, inside each `install_manpage` call, before that call's `txn.put`; an earlier page in the loop can succeed and overwrite its durable target with new bytes before a later page's call raises and the whole transaction's write is abandoned.
The entry for that earlier page still names the *old* checksum, because the write that would have updated it never committed, so a later `maniac uninstall` reads a page nothing is wrong with as MODIFIED.
The Postscript declined to fix this itself -- "repairing that needs a cross-page filesystem undo log -- a new mechanism and its own decision, not a correction to this one" -- and filed the marker as `BUG:` (now at `orchestration/install.py:279`, moved from `:211` when `lifecycle.reconcile` was deleted).
`docs/BACKLOG.md` named two candidate directions without choosing between them: the undo log the Postscript described, or re-verifying checksums against disk on the manifest's next transaction.

Investigated this session, 2026-09-21, before choosing:

`manifest.Transaction`'s atomicity is exactly as documented and was confirmed at the code, not just the docstring: an exception leaving the `with manifest.transaction(...)` block never reaches the `save()` call, so the manifest side of a failed grouped install is genuinely all-or-nothing.
The undo primitive this bug needs already exists, proven, just scoped one call too narrow.
`installer._materialize_and_link` already wraps one page's materialize-and-link step in a try/except that calls `_discard_materialized_target` and `_restore_or_discard_backup` to reverse that call's own filesystem effect on failure, and its docstring already claims "a failure here leaves nothing behind."
That claim is true for the page that itself fails.
It is not true for an earlier page in the same loop that already succeeded cleanly -- no exception was ever raised for it, so its own undo never fires, and nothing today asks it to run retroactively when a sibling page fails later in the same transaction.

The checksum re-verification alternative has no comparable head start.
The structural link scan (`manifest.scan`/`Link`) that `docs/BACKLOG.md` pointed to as already having "the shape for" this is `lstat`/`readlink` only -- it never reads file bytes -- so a byte-level checksum comparison against disk would be new code, not an extension of something proven.
It would also need a policy decision this project has otherwise avoided: telling apart "this file drifted because our own aborted reinstall left new bytes under an old entry" from "the user edited the installed page after MANIAC put it there," since only the first is safe to heal silently and nothing on disk distinguishes them.
ADR-0020's refusal to guess at binary identity without evidence, and ADR-0043's choice to make the manifest the one source of truth rather than something reconciled against filesystem evidence after the fact, both argue against adding that judgment call.

This is also not the crash-recovery case ADR-0046 already closed by adoption.
A crash mid-loop leaves an unrecorded symlink under `output_dir`, which `_adopt_orphans` already recovers on the next transaction -- indistinguishable from any other adoption case.
This bug's case is the process staying alive: the transaction fails in-process and unwinds normally, and the earlier pages' entries were never wrong -- they were about to be *replaced* by the values they already correctly hold, except the transaction never got to commit that replacement because a sibling page failed later in the same loop.
The gap is purely "make the filesystem match what the manifest is about to (still) say," entirely within one process's live call stack.

## Decision

Undo, not heal.
When a page in `_try_repository`'s per-release loop fails, reverse the filesystem effect of every earlier page that same loop already installed, before the exception finishes propagating out of the transaction.
This extends `_materialize_and_link`'s existing single-call undo across the loop rather than building a second mechanism: the per-call rollback (`_discard_materialized_target`, `_restore_or_discard_backup`) is already correct and already exercised; what is missing is that a caller making more than one `install_manpage` call inside one transaction has to retain enough about each success to run that same rollback for it later, in reverse install order.

`install_manpage` returns a small record instead of a bare `Path`, carrying the materialized path alongside the internal `Materialized` and backup state the undo primitives need.
`_try_repository`'s loop keeps the record from every page it has installed so far; on a later page's exception, it undoes them in reverse order using the same two functions `_materialize_and_link` already calls on its own failure.
The other two callers of `install_manpage` (`_try_install_root`, `pipeline.py`) each install exactly one page and only ever needed the path, so they read `.path` off the same record -- a mechanical update, not a design change, for them.

The "still used by another entry" check inside `_discard_materialized_target` must run against the transaction's entries as they stood *before this loop's own puts*, not the live in-flight working set.
`txn.entries` is one shared dict, mutated in place by every `install_manpage` call the loop makes through `manifest.joined`, so by the time a third page fails, that dict already holds the first and second pages' freshly-put entries.
Checking "is this target still referenced" against it at undo time would see the very entry now being undone and wrongly refuse to unlink its target.
The loop snapshots `entries` once, before its first `install_manpage` call, and checks target usage against that snapshot during undo -- the transaction's own exit still discards every put regardless, per ADR-0046; the snapshot only answers the target-in-use question correctly, it is not a substitute for that discard.

This is not a journal, and does not reopen ADR-0046's rejection of one.
Nothing here is written to disk or survives the process: it is an in-memory rollback confined to the same call stack that made the writes, unwound synchronously before `_try_repository`'s exception reaches its caller.
ADR-0046 rejected a journal as a second persistent source of truth kept to reconcile the filesystem against the manifest across a *crash*; this fixes a case where the process never crashes at all, and the manifest was never wrong even for an instant -- only unable to commit a replacement it was correct to attempt.

Checksum re-verification / healing is rejected outright for this problem, not deferred alongside it.
It would treat a symptom this fix removes at the source, and it would need the "is this drift ours to fix" policy judgment named above, which this project has consistently declined to build elsewhere.
If a checksum still drifts after this lands, through some path this record did not anticipate, that is new evidence for revisiting the question -- not a reason to build the healing pass preemptively now.

## Consequences

A grouped reinstall that fails partway now leaves the filesystem exactly where it started, matching what the manifest (correctly, and now provably) already says: no MODIFIED report on a later uninstall for a page that was only ever going to be rewritten with content nothing else had changed.

`install_manpage`'s return type changes from `Path` to a small record.
All three call sites need touching; two of them (`_try_install_root`, `pipeline.py`) trivially, by reading `.path`.

`_discard_materialized_target` and `_restore_or_discard_backup` gain a second call path -- the loop-level undo -- beyond `_materialize_and_link`'s own except block.
Both already take exactly what a second caller needs (`Materialized | None`, `cfg`, an entries mapping to check target usage against, a backup path, a destination file), so nothing about their own signatures has to change, only which `entries` snapshot each call site passes.

The `BUG:` marker at `orchestration/install.py:279` and its `docs/BACKLOG.md` item close once this lands.

Tier 1 (`_try_install_root`) and tier 3 (`pipeline.py`) are untouched in substance: both make exactly one `install_manpage` call, so there is nothing for either to undo across, unchanged from the Postscript's observation that a single call was already correct on its own.
