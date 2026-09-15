# ADR-0044: One locked transaction per manifest operation, and no journal

Status: Accepted
Date: 2026-09-15

## Context

ADR-0043 settled that the manifest is an explicit protected record and named the protection
it implied, without building it.
A full map of the machinery then found the problem was worse than the four findings that ADR
was written against.

`record` and `forget` were each a complete load-modify-save.
Installing a multi-page release performed one such cycle per page plus `reconcile`'s own, and
nothing serialized them: two concurrent installs silently dropped one another's entries while
both symlinks remained on disk.

Worse, the manifest was written *last*.
`install_manpage` backed up the displaced page, materialized the durable target, linked the
manpath entry, and only then recorded.
Every crash window therefore left the filesystem ahead of the manifest.
A crash between linking and recording made MANIAC's own page read as foreign, so the next
install would back up MANIAC's work as if it were the user's.
A crash between backing up and recording left the user's vendor page in `backup_dir` with
nothing naming it, unrestorable and overwritten by the next `--force` install.
A crash partway through a multi-page release left a page on the manpath that no entry knew
about, permanently.

None of these were pinned by any test, and nothing anywhere ran two writers.

## Decision

**One transaction per operation.**
`manifest.transaction(config)` is a context manager that acquires an exclusive lock, reads
once, promotes the checkpoint, yields a working set, and writes once on exit.
An exception discards every mutation.
`record` and `forget` are deleted rather than kept as wrappers; `lifecycle.reconcile` takes
the transaction and mutates its live entries instead of loading and saving its own.

This is what makes a multi-page release atomic — its entries land in one write or none — and
it is what closes the concurrent lost-update window.

**The lock is `fcntl.flock` plus a process-local `threading.Lock`**, the pattern already
proven in `sources/docs/cache.py`.
No dependency was added; the project had no file-locking library and did not need one.

**The lock spans the commit phase only.**
LLM synthesis, pandoc and crawling touch nothing the manifest owns.
`install_manpage` computes its checksum and takes its source before opening the transaction,
so generation stays outside the lock and external parallel installs remain possible — which
ADR-0043 requires and which is the whole reason a database transaction was not the answer.

**No write-ahead journal.**
A journal was the obvious way to make the filesystem and the manifest agree across a crash,
and it was declined.
The same guarantee is available more cheaply by making the artifacts self-describing enough
to be adopted: a symlink into `output_dir` with no entry is a crash-after-link and is adopted
on the next transaction, and a backup named after the page it displaced is traceable without
any separate record of intent.
A journal would have added a second source of truth to protect, which is the opposite of
ADR-0043's direction.

**`put` takes an `Entry`.**
Replacing `record` meant choosing a signature, and re-declaring its twelve parameters was the
worse option — `docs/BACKLOG.md` already carries that signature as a defect.

## Consequences

Two writes per operation replace N+1.

`lifecycle._seed_from_headers`, `list_installed_manpages` and `read_provenance_header` are
deleted: recovery now reads link targets, which ADR-0028 made the stronger evidence.

The structural scan runs on every transaction open, not inside `load`/`read`, which stay pure
deserialization per ADR-0034.
This is narrower than intended — the scan is itself read-only and could run on every load, so
that `maniac list` reports drift rather than only write paths noticing it.
Recorded in `docs/BACKLOG.md`.

Tier-1 direct provider links are not reconstructible.
"Not under `output_dir`" is not evidence of a provider root, and adopting one would let
MANIAC replace and later remove a symlink the user owns.
Refusing to guess is consistent with ADR-0020 and ADR-0029; the cost is that a lost manifest
loses tier-1 ownership entirely.

Reconstructed entries claim `Tier.SYNTHESIS` with `source="reconstructed"`.
The source is honest and the tier is not — `output_dir` holds tier-2 and tier-3 pages alike
and nothing on disk separates them.

An ADR-0032 install-root migration interrupted between relinking and its manifest write stays
stuck: the eligibility guard skips it forever and uninstall reports it MODIFIED.
Marked `BUG:` at `lifecycle.py:276` and recorded in `docs/BACKLOG.md`.
