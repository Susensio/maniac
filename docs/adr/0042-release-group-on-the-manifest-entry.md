# ADR-0042: A release group is recorded on the manifest entry, not inferred

Status: Accepted
Date: 2026-09-15

## Context

A GitHub release asset can carry several manpages for one tool.
ADR-0027 gave each extracted page its own exact provenance URI, and `orchestration.install`
installs them as one `install_manpage` call per page, keyed by `_manpage_owner`.
eza is the live case: one release yields `eza.1`, `eza_colors.5` and
`eza_colors-explanation.5`, recorded as three independent manifest entries.

Nothing recorded that they arrived together.
Uninstalling `eza` removed `eza.1` and left the two `.5` pages orphaned on the manpath,
with their displaced vendor pages unrestored.

The obvious substitute was inference from `source_uri`.
`release._manpages_from_release_archive` stamps the same asset URL on every extracted page,
and the repository-tree probe returns at most one page, so a shared `source_uri` does
identify a multi-page bundle in practice.

## Decision

Group membership is recorded explicitly on `Entry.group`, holding the manifest key of the
release's primary page and carried by every member including the primary itself.
Uninstalling any member removes the whole unit — companion pages and displaced vendor
pages, each checksum-protected before removal and each restored on the way out.

`source_uri` was rejected as the grouping key.
It records which upstream file a page came from, not that several pages arrived together,
and it cannot express which member is primary.
Uninstalling `eza_colors` would therefore be structurally indistinguishable from
uninstalling `eza`, and two binaries from one repository whose pages came from the same
asset would be bundled with nothing to tell them apart.
A field that is right for the common case and silently wrong for the adjacent one is worse
than no field, because the failure is a wrongly deleted page.

`SCHEMA_VERSION` stays at 1.
The field is purely additive: `_row_to_entry` reads it with `.get`, so a row written before
it existed loads ungrouped and behaves exactly as before — the same migration shape ADR-0018
established for `version`.

Bumping it would have been actively harmful, which is the more important half of this
decision.
`manifest.load` returns `{}` for any version it does not recognize, so a bump makes every
existing installation's manifest read as "MANIAC owns nothing" on the next run.
The following `install --force` would then back up MANIAC's own pages as though they were
the user's.
A schema version that cannot be incremented without destroying ownership is not a working
migration mechanism; that defect is recorded in `docs/BACKLOG.md` under the manifest
contract item and is not fixed here.

## Consequences

Uninstall is whole-unit for release bundles, and `UninstallResult.modified_kept` became a
list because protection is now decided per member rather than once.

The manifest now holds one fact that is genuinely not derivable from the filesystem.
Ownership, link targets and provider-owned status can all be recovered by inspecting
symlinks under ADR-0028; two pages sitting in `output_dir` carry no evidence they arrived
from the same release.
Any future move toward treating the manifest as a rebuildable cache of filesystem-derived
facts must keep `group` as stored state, or find evidence for it that does not exist today.

A group whose upstream release later drops a page keeps a stale member recorded; install
has no group-pruning pass.
Recorded in `docs/BACKLOG.md`.
