# ADR-0028: Link managed manpath entries instead of copying pages

Status: Accepted
Date: 2026-09-13

## Context

ADR-0017 had made the manifest authoritative for ownership while `install_manpage` still copied every page into the shared manpath.
That duplicated generated output that MANIAC already owned durably and froze vendor documentation at its installation-time version.
Repository pages could not safely link into the cache because cache eviction would turn a working manual into a dangling link.

Keeping copies also made an upgrade leave a vendor manual stale until MANIAC ran again.
The equivalent Mise system-install workflow had established that a provider-managed page can advance with its installation when the target is stable.

Existing manifest entries were copies as well.
A change for new installations only would leave their ownership, checksum protection, vendor backups, and uninstall behaviour on the old storage model indefinitely.

## Decision

Every manpath entry owned by MANIAC is a symbolic link whose expected target is stored in the manifest.

Vendor pages link directly to a provider-managed page only where that provider supplies a target that remains valid or advances across an upgrade.
Generated pages link to MANIAC-owned durable output.
Repository pages are first materialized in a durable MANIAC data location and then linked from the manpath; the disposable repository cache is never a link target.

Installation atomically establishes the durable target before replacing the manpath entry with its link.
Uninstallation removes only an entry that is still the expected symbolic link, preserves a replaced, retargeted, or dangling path for the user, and restores a recorded displaced page only after its owned link is removed.

The manifest migration converts every existing managed entry whose provenance can be materialized safely, preserving its bytes, checksum, backup, tier, and source metadata before replacing the copied manpath page.
An entry without a safe durable target is retained unchanged and reported rather than guessed or deleted.

## Consequences

MANIAC no longer duplicates bytes on the manpath, and generated and repository pages have ownership boundaries that survive cache cleanup.
Eligible vendor manuals can follow their managed installation's update path without a new MANIAC install.

The manifest acquires target identity in addition to content identity, and every lifecycle operation must handle links deliberately rather than treating a path as ordinary file content.
Migration is necessarily conservative: an old page whose source cannot be recovered stays usable as a copy until the user reinstalls it.
