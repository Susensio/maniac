# ADR-0058: Give install_manpage a PageRequest of caller-known fields instead of a placeholder-filled manifest Entry

Status: Accepted
Date: 2026-09-24

## Context

Collapsing `install_manpage`'s parameter list into a manifest `Entry` (`62b6881`, 13 arguments to 8) left one type serving two roles.
Callers built their argument with `draft_entry`, which returned a real `Entry` whose `path` was `Path()` and `checksum` was `""`, with `backup` and `target` unset, because only the install itself could know those four.
`install_manpage` overwrote them with `dataclasses.replace` before anything read or stored them.
No defect came of it: a check on 2026-09-24 found no caller or test reading a placeholder field.
The only guard was a docstring saying nothing read them, so the type could not say whether a given `Entry` was a request or a record, and a well-formed `Entry` could describe no installation at all.
It sat in `docs/BACKLOG.md` as a wart, flagged as touching ADR-0046's manifest boundary.

Three shapes were weighed.
Leaving it was defensible, since nothing was broken and the reasoning was written down.
Nesting the caller-known fields inside `Entry`, so a record is a request plus what install learned, read well but changed the manifest's serialized JSON and reopened ADR-0046 for a cosmetic gain.
A separate caller-facing type that `install_manpage` turns into an `Entry` changed neither `Entry` nor the stored format.

## Decision

`install_manpage` takes a frozen `PageRequest` holding exactly what the caller knows -- `tier`, `source`, `version`, `source_uri`, `provider_target`, `group` -- and constructs the full `Entry` itself once the destination, checksum, backup and target are known.
`draft_entry` and its placeholders are deleted.
`Entry` and the manifest format are unchanged, so ADR-0046's boundary is not touched.

The name `PageRequest` was chosen over `InstallRequest`, which read as a request to run an install and echoed `InstallResult`, and over anything built on "provenance", which already names the page header MANIAC stamps.

## Consequences

An `Entry` now only exists once an install has produced it, so a placeholder record cannot be built, passed around or stored by mistake.
Six fields are declared twice, on `PageRequest` and on `Entry`; a field added to one has to be added to the other, and the type checker catches a mismatch only where `install_manpage` constructs the `Entry`.
If `Entry` ever changes shape for other reasons, nesting a `PageRequest` inside it is the natural next step and the duplication goes away.
