# ADR-0017: Track installed pages in a state manifest instead of a provenance header in the page

Status: Accepted
Date: 2026-09-08

## Context

[ADR-0016](0016-authoritative-manpages-first.md) introduced three source tiers and ruled that a page found at tier 1 (the install root) or tier 2 (an upstream repository) is installed as it stands.
Only tier 3 — synthesis — produced a page MANIAC had authored, and only that path wrote a provenance header: two `.\"` comment lines naming the tool, the date and the model, prepended by `compile_to_man` after Pandoc ran.

MANIAC's bookkeeping was built before tiers 1 and 2 existed, when every installed page was synthesized and therefore carried that header.
`install_manpage` copied a page with `shutil.copy2`, a byte-for-byte copy, so a page taken from an install root arrived with no header and no way to acquire one.
The bookkeeping had no other record to consult: nothing was written to a state directory, and the installed file itself was the entire inventory.

The three commands that depend on knowing whether MANIAC owns a page had drifted apart under that pressure.

`uninstall_manpage` read the header and treated its absence as proof of a vendor page, reporting "foreign, kept in place".
For every tier-1 and tier-2 page this was wrong, and wrong in the direction that leaves files behind.

`status` did not read the header at all.
`_state_for` called `find_managed_manpage`, which tested only for `<tool>.1` plus a compression suffix inside `man_dir`, and its docstring recorded the reason as deliberate: re-deriving the state from the install root would have reported an already-installed tier-1 page as not yet installed.
This traded one error for another, since `man_dir` is `$XDG_DATA_HOME/man/man1`, a standard manpath directory shared with anything else that writes there, so a page MANIAC never touched reported as `MANAGED`.

`install_manpage`'s conflict guard read the header too, to decide whether an occupying page needed a `.maniac_bak` backup before being overwritten.

Two further facts constrained the repair.
`read_provenance_header` opened its file with `path.open()` and no decompression, so it could not have read a header out of a compressed page even had one been written there.
And `uninstall_manpage` looked only for `cfg.man_dir / f"{tool_name}.1"`, with none of the compression-suffix loop `find_managed_manpage` already carried, while `install_manpage` copied to `src.name` — so a tier-1 `pandoc.1.gz` was not merely misreported by `uninstall` but invisible to it.

Writing a header into the copied page was the obvious repair and was weighed seriously.
It reuses a mechanism already in the tree, it is invisible in rendered output, and it keeps one place to look.
Against it: a compressed page such as `pandoc.1.gz` would have to be decompressed, modified and recompressed, so the bytes on disk would stop being upstream's.
The rendered page would be unchanged and no user would see a difference, but the claim that tier 1 installs what upstream shipped would stop being literally true — and that claim is ADR-0016's stated reason for preferring tier 1 over synthesis.
It also required teaching `read_provenance_header` to decompress, and the header's two-line format had no field for the tier or the source a manifest would need to carry anyway.

## Decision

A manifest in MANIAC's state directory becomes the authoritative record of which pages MANIAC installed.
It maps a tool to its installed path, the tier the page came from, and the source it came from.

`install`, `uninstall` and `status` decide ownership by consulting the manifest and nothing else.
A page absent from the manifest is not MANIAC's, whatever its content, and a page present in it is MANIAC's, whatever its bytes.
This replaces `uninstall`'s header check, `install`'s conflict-guard header check, and `status`'s location-and-filename test in `find_managed_manpage`.

Pages installed at tiers 1 and 2 are not modified.
No page acquires a header it did not arrive with, and no page is decompressed and recompressed in order to be tracked.

The provenance header stays on synthesized pages, and stops being authoritative.
It remains as self-describing metadata for a human reading the roff source and as the one record that survives the loss of the state directory, but no command branches on it.

The manifest is the lookup path for an installed page, replacing the filename reconstruction `uninstall_manpage` performed.

## Consequences

The round trip closes.
A page installed from any tier can be uninstalled, and the compression-suffix hole that made a tier-1 `pandoc.1.gz` unreachable closes with it, because the path is recorded rather than guessed.

`status` stops reporting a page MANIAC never installed as `MANAGED`.
This is a visible behaviour change for anyone who keeps their own pages in `$XDG_DATA_HOME/man/man1`, and it is a correction: those pages were never MANIAC's.

Two provenance mechanisms exist where there was one, which is the cost of this decision.
It is bounded by only one of them being consulted, so they cannot disagree about an outcome; a header that contradicts the manifest is stale metadata, not a second opinion.

MANIAC acquires durable state, and with it the failure modes state has.
A manifest that is deleted, corrupted or out of step with the disk orphans every page it described — recoverable for synthesized pages by reading their headers, and not recoverable for tier-1 and tier-2 pages, which carry nothing.
Reconstruction is not designed here.

Vendor bytes stay untouched, so ADR-0016's tier-1 claim stays literally true rather than true only of the rendered output.

The unresolved question is when the manifest is written relative to the copy, since a crash between the two leaves a page on disk that MANIAC does not know it owns.
Ordering the write before the copy inverts the failure into a manifest entry with no file, which `status` can detect and a stale file cannot.
This is not settled here.
