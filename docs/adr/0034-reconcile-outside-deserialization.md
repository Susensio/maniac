# ADR-0034: Reconcile the manifest outside deserialization

Status: Accepted
Date: 2026-09-14

## Context

ADR-0028 made every managed manpath entry a tracked symbolic link, and ADR-0032 migrated eligible unchanged install-root copies to direct provider links.
Both migrations ran as an effect of `manifest.load()`, reaching into `installer` through a deferred import placed there to break the resulting cycle.

Deserializing a JSON file therefore replaced manpath links and removed durable targets.
`maniac list` is a read-only command, and `lookup()` is its read path, so listing installed tools could rewrite the user's manpath.
The deferred import was the visible symptom; the defect was that reconciliation had no home of its own and had settled in the only code every caller already ran.

## Decision

`manifest.load()` parses and validates persisted ownership facts and does nothing else.
`_save` becomes public `save`, so writing is a caller's explicit act rather than a private effect.

A new `maniac/lifecycle.py` owns reconciliation as one seam: provider-page discovery, ownership and checksum checks, link replacement, durable-target cleanup, and the manifest updates that follow.
Install and uninstall each call `lifecycle.reconcile(cfg)` explicitly and judge ownership from the entries it returns.

Legacy-copy recovery and install-root direct-link migration keep their conservative rules unchanged.
What changes is when they run: on a write path that asked for them, never as a side effect of a read.

ADR-0028's link-not-copy invariant, ADR-0031 and ADR-0032's direct-link and migration rules, and uninstall's guarantee never to remove a provider-owned target are all preserved rather than revisited.

## Consequences

Read-only callers observe the manifest as persisted, and a migration-eligible entry stays un-migrated until the next write path runs.
A user who only ever runs `list` will not have historical entries migrated, which is the honest reflection of a command that promises to report rather than to change.

The `manifest`/`installer` cycle is gone, so read-only callers have a locally testable interface.
Filesystem transitions are now tested at the lifecycle seam against real temporary directories instead of being reached by calling `load`.

`test_load_does_not_migrate_a_legacy_entry` pins the invariant by snapshotting every file's mtime and size beneath the temporary root and asserting `lookup()` leaves the tree untouched.
That test is the decision; without it the migrations would drift back into load the next time a caller finds it convenient.
