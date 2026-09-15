# ADR-0043: The manifest is an explicit protected record, not a derived cache

Status: Accepted
Date: 2026-09-15

## Context

The manifest is the sole record of which manpages MANIAC owns, which files it displaced to
install them, and what it must restore on uninstall.
Everything destructive MANIAC does is authorized by it.

Several findings arrived together and forced the question of what kind of artifact it is.
Concurrent installs can lose ownership entries through an unserialized load-modify-save.
`save()` never `fsync`s before its atomic rename, so a power loss can land the rename ahead
of the data.
`load()` returns `{}` for a file that is absent, unparseable, or carries an unrecognized
schema version, and the next `record()` then writes a manifest holding only the tool just
installed -- replacing a salvageable file with a one-entry one.

Two directions were weighed against keeping it as it is.

**Move it to SQLite.**
Rejected.
It does not solve the corruption case: the existing write-to-temp-then-rename already
prevents torn files, and the remaining gap is a missing `fsync`, which is three lines.
It does not remove the need for an application-level lock, because a database transaction
cannot be held open across the filesystem and network work an install performs.
It costs inspectability -- no `cat`, no `git diff`, no hand repair -- and turns one file
into three, so a naive backup of the main file alone captures a stale snapshot.

**Make it implicit in the filesystem.**
Rejected, though it was closer than it looks.
ADR-0028 made every manpath entry a symlink, so a target under `output_dir` proves MANIAC
materialized it and a target under a provider root is a tier-1 provider-owned page; that
recovers ownership, `path`, `checksum`, `target` and `provider_target` without any stored
state.
The remaining fields each had a plausible home on disk: `version` in the durable target's
filename, `source_uri` stamped into the roff header of the tiers MANIAC writes, `backup` in
a filename naming the entry it displaced.

The reason to decline is not feasibility.
It is that encoding a record into filenames and headers makes the schema implicit,
unreadable and awkward to change, trading a file a person can open for a convention a person
must reverse-engineer.
ADR-0042 also established that at least one fact -- release group membership -- has no
filesystem evidence at all, so the manifest could never fully dissolve regardless.

## Decision

The manifest stays what it is: an explicit, human-readable JSON document that is the source
of truth, not a cache of facts derivable from elsewhere.

It is treated as the cornerstone it has become.
It is protected against loss rather than made cheap to lose: serialized writes, durable
writes, and retained previous generations so a bad state can be stepped back from.

Filesystem reconstruction is retained, but demoted to what it actually is -- a best-effort
last resort for a case that should never arrive, not a design the normal path depends on.
When it runs it should use the strongest evidence available, which after ADR-0028 is the
symlink target rather than the provenance header `lifecycle._seed_from_headers` reads today.

A corrupt or unreadable manifest is never silently treated as an empty one.
Reading zero entries and knowing there are zero entries are different facts, and only the
second authorizes MANIAC to treat an existing managed page as foreign.

## Consequences

Concurrency is solved with a lock rather than a storage engine, and the lock must not span
generation.
LLM synthesis, pandoc and crawling touch nothing the manifest owns; only backup, link and
record do, and those are filesystem-fast.
Install therefore splits into a generate phase (unlocked, slow, safe to run in parallel
across processes) and a commit phase (locked, milliseconds).
A generated page is materialized before the lock is wanted, so a failed commit costs a
retried rename, never a repeated model call.

`SCHEMA_VERSION`'s current semantics contradict this decision and are left unresolved here.
An equality check that degrades to `{}` is a tripwire with no handler: it cannot be
incremented without making every existing manifest read as empty, which is why ADR-0042
declined to bump it and shipped additively instead.
Recorded in `docs/BACKLOG.md`.

The durability, locking and checkpointing work this authorizes is not yet built.
This ADR settles which artifact the manifest is, so that work has a target to aim at.
