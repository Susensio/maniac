# ADR-0053: Uninstalling a companion page refuses and redirects to the primary

Status: Accepted
Date: 2026-09-21

## Context

ADR-0042 gave a release group a primary key on `Entry.group`, and made uninstall reach the whole group from any member: `maniac uninstall eza_colors` removes `eza.1` too, restoring each member's own displaced vendor backup along the way.
That is defensible on its own terms -- the three pages are one installation, and leaving `eza` without its companions is exactly the half-installed state grouping exists to prevent -- but nobody types `maniac uninstall eza_colors` meaning "also remove eza".
`eza_colors` is a manifest key derived from a filename that arrived bundled with `eza`'s release, not something the user chose to install by that name, so answering the command by silently deleting a page it never named teaches the wrong mental model of what happened.

`docs/BACKLOG.md` named three candidates: keep symmetric removal as-is; refuse the companion and redirect to the primary, `apt`-style; or keep symmetric removal but report the full set and require confirmation first.
The backlog flagged a cost specific to refusal: a user whose primary entry is already gone by some other means (crash, manual manifest edit) would have no way to remove an orphaned companion, so whichever wins needs an escape hatch for that case.

Reading the CLI before choosing: `maniac install` already has exactly this shape.
`InstallRefused` (`maniac/orchestration/install.py`) is raised before any tier runs, rendered in yellow rather than red, and -- per ADR-0048 -- still exits non-zero even though nothing failed mid-operation.
`--force` is the project's one interaction pattern for "I mean it anyway", and it is a flag checked at the top of a function, never an interactive prompt: nothing in this codebase calls `typer.confirm` or checks `sys.stdin.isatty()` today.
Introducing a confirmation prompt for uninstall would be a new interaction pattern with no precedent to match, and it would need its own non-interactive/`--yes` story invented from nothing.

## Decision

**Refuse and redirect** (candidate 2), reusing the `InstallRefused` shape rather than inventing a new one: `UninstallRefused(ManiacError)` in `maniac/installer.py`, raised by `_refuse_companion_uninstall` before `uninstall_manpage` opens its manifest transaction, and rendered by `cli/uninstall.py` in yellow with `typer.Exit(1)` -- a refusal, not a crash, but still a run that leaves the world unchanged from what was asked, so ADR-0048's reasoning about the exit status applies here as much as it does to install.

`maniac uninstall eza_colors` now reports:

    'eza_colors' is part of 'eza's installation (bundled in the same
    release, ADR-0042) -- run 'maniac uninstall eza' to remove the whole
    group.

**The escape hatch falls out of the refusal's own condition, with no new flag.**
`_refuse_companion_uninstall` only refuses when the primary entry is *still present* in the manifest (`entry.group in entries`).
A companion whose primary already vanished by some other means has nothing to redirect to -- the refusal exists to keep the group's members reachable together while a working primary anchors them, not to trap an orphan that has already lost that anchor.
In that state, naming the companion behaves exactly as symmetric removal always did: it removes itself and whatever other companions still share the (now-primary-less) group, computed by the existing `_group_members`/`_uninstall_group` machinery unchanged.

A forgotten manifest row alone does not reach this state if the primary's page is still linked: `manifest.transaction`'s orphan adoption (ADR-0046) re-adds an unrecorded manpath symlink to `entries` the moment the uninstall's own transaction opens, which puts the primary right back into the group's reach (ungrouped itself, but still swept up as the reachable primary key). The escape hatch therefore answers "the primary's page is genuinely gone", not merely "the manifest forgot it" -- which matches the backlog's own crash/manual-edit framing better than a manifest-only definition would, since a manifest edit that leaves the page linked is exactly the case adoption already exists to repair.

This was checked against candidate 3 (confirm-and-proceed) directly: it would have needed a `--yes`/non-interactive bypass invented for this one command, with no sibling command's behavior to copy, and confirmation still uninstalls a page the user didn't name on a keypress -- refusal teaches the same lesson without ever taking that action by accident.

## Consequences

`uninstall_manpage` gains one refusal check, run against a plain `manifest.load(cfg)` before the transaction opens -- mirroring `_refuse_unmanaged_destination`'s own cheap-check-before-transaction shape in `orchestration/install.py`, including its same small TOCTOU window between the check and the transaction's own read, accepted there for the same reason: a hard refusal, not a data-destroying decision, so losing a race just means the next run refuses again with the same message.

`_group_members` and `_uninstall_group` are unchanged: whichever `tool_name` reaches them (a primary, or an orphaned companion with no primary left to redirect to) already computes the correct set today.

A primary itself is never refused -- `entry.group == tool_name` is the ordinary primary-uninstalls-itself case ADR-0042 already covered, and stays symmetric exactly as before.

Nothing changed about *what* uninstall removes once it proceeds, or how a modified/foreign/retargeted member is reported; only the entry point for a companion request gained a gate in front of it.
