# ADR-0051: Give grouped-install undo its own restore-and-discard step, instead of reusing the single-call primitives' existence check

Status: Accepted
Date: 2026-09-21
Supersedes: [ADR-0050](0050-grouped-install-filesystem-undo.md)

## Context

ADR-0050 was implemented the same day it was accepted (`e2846dc`), reusing `_discard_materialized_target` and `_restore_or_discard_backup` as-is -- the two primitives `installer._materialize_and_link` already calls on its own single-call failure -- across `_try_repository`'s per-page loop.
Verifying the implementation against a live scenario, not just the new unit tests, found that reuse was unsound for one real case, and separately confirmed a limitation ADR-0050 did not examine.
Both are recorded here rather than folded silently into the code, since ADR-0050's Decision specifically claimed "the per-call rollback... is already correct and already exercised" and "a mechanical update, not a design change" -- both claims this record narrows.

**The unsound reuse.**
`_restore_or_discard_backup`'s choice of restore-versus-discard is `_path_exists(dest_file)`: if the manpath entry still exists, assume the install never got that far and the backup is redundant; if it is gone, assume this call's own `link_manpath_entry` never ran and there is nothing to put the backup back over.
That check is sound only for the call whose own stack frame is unwinding, because `lifecycle.link_manpath_entry` is atomic (`temporary_link.replace(path)`; on any error it removes its own temp file and never touches `path`) -- so a single call's except block only ever runs with `dest_file` in exactly the state it was in before that call started.
The loop-level undo breaks that invariant on purpose: it calls these two functions for a page whose `install_manpage` call already returned successfully, meaning `link_manpath_entry` already completed its atomic replace.
`dest_file` is not in its pre-call state at undo time -- it is a live symlink at the target `_discard_materialized_target` is about to remove.
Unlinking only the target leaves `dest_file` dangling; `_path_exists` reports a dangling symlink as present (`.is_symlink()` is true even when the link target is gone), so `_restore_or_discard_backup` takes the "still occupied, discard the backup" branch and permanently deletes the one surviving copy of whatever page this install displaced.
Confirmed by tracing `link_manpath_entry`'s atomicity and `_take_backup`'s `shutil.copy2` (copy, not move, so the original stays in place until the atomic replace) directly against the code, and confirmed there is no existing test exercising the single-call except-block's `dest_file` state, because the atomicity guarantee makes it structural rather than something a test would need to pin.
This is not a hedge on probability -- it is not "rare," it is any grouped install where an earlier page in the loop displaced a foreign or vendor page under `--force` and a later page then failed.

**The examined-and-accepted limitation.**
Separately, reinstalling a page over a target this same release already owns -- the ordinary case a version bump produces -- takes no fresh backup at all: `_take_backup` carries the *existing* entry's backup pointer forward rather than copying the about-to-be-overwritten bytes, because until now nothing needed to reconstruct them.
`_discard_materialized_target`'s target-still-used check, run against the correct pre-loop baseline, correctly refuses to unlink a target this page's own prior entry still legitimately claims -- but nothing then restores that target's *previous bytes*, because no copy of them was ever taken.
So for exactly the case ADR-0050's Context opened with -- a version-bump reinstall reusing its own prior target -- the undo this record and ADR-0050 together build does not close the checksum-drift gap that motivated the work: the manifest's old checksum still describes bytes the aborted reinstall already overwrote.
Closing it would mean copying a page's own current bytes before every ordinary overwrite, on the chance a *sibling* page fails later in the same loop -- new I/O cost on every successful reinstall, paid for a benefit that only matters on the rare partial-failure path, and a trade-off neither ADR-0050 nor this record chooses to make.
This is accepted as a known, bounded gap rather than fixed here: a first-time multi-page install and a reinstall that displaces something *other than this same release's own prior page* are the cases this work does close, and they are the more common ones — the checksum-drift symptom the Postscript named is *reduced*, not eliminated, and revisiting it needs its own decision if the residual case is measured to matter in practice.

## Decision

The loop-level undo gets its own restore-and-discard step instead of calling `_restore_or_discard_backup` unmodified.
It does not need `_path_exists(dest_file)` at all, because it already knows -- by construction, since only a page whose `install_manpage` call returned successfully is ever in the loop's undo list -- that `link_manpath_entry` completed and `dest_file` is currently the symlink now being torn down.
Given that certainty, the rule is unconditional rather than a check: unlink `dest_file` outright, then if the page's record carries a `backup_path`, move it back over `dest_file` (`shutil.move`, the same call `_restore_or_discard_backup` already makes on its own restore branch); if there is no backup, `dest_file`'s removal alone is the correct end state, matching "nothing was there before this page's call."
`_discard_materialized_target`'s own logic is untouched by this record -- its target-still-used check against the pre-loop baseline (ADR-0050) remains correct, because that question is about the durable target, not the manpath symlink, and nothing about this correction changes it.

`_materialize_and_link`'s own single-call except block is untouched.
Its use of `_path_exists(dest_file)` remains correct for what it was built for -- undoing a call whose own frame is failing, where `dest_file` was never mutated -- and this record does not generalize it or rename it; the loop-level undo simply stops relying on it and does the unconditional version instead.

The reused-own-target checksum-drift gap is not fixed.
It is written down here, explicitly, as what this work does not close, so a future reader measuring residual MODIFIED reports after this lands knows which case is still open rather than re-discovering it.

## Consequences

A grouped install that fails partway now correctly restores a displaced foreign or vendor page's backup, rather than risking its permanent loss -- this closes a real regression the ADR-0050 implementation introduced before it ever reached `master` unremediated in that form.

`_try_repository`'s undo step is slightly larger than ADR-0050 described: an explicit unlink of `dest_file` plus an unconditional (not existence-gated) backup restore, rather than a bare call to the existing single-call function.
No new primitive is added to `installer.py`'s public shape beyond what ADR-0050 already introduced (`InstallResult`); this is a correction to how the loop uses it, not a new mechanism.

A version-bump reinstall of a multi-page release that fails partway can still leave one page's manifest entry undercounting a checksum that changed -- unchanged from before ADR-0050, not made worse, and now documented as an accepted limitation rather than an implied-fixed case.
`docs/BACKLOG.md` should carry this residual gap as its own item if it is worth tracking separately, rather than as part of the item ADR-0050 already closed.
