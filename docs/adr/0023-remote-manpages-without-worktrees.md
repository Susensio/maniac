# ADR-0023: Discover versioned repository and release manpages without worktrees or Mise hints

Status: Accepted
Date: 2026-09-11

## Context

ADR-0018 made `list` probe the version-matched upstream repository before deciding that a page was missing.
That probe used the same shallow working-tree clone as documentation synthesis, even though it needed only the repository tree and at most one manpage blob.
On the development system, checking out ruff failed because an eCryptfs filename limit rejected long snapshot-fixture names.
The failure hid an authoritative page from tier 2, and the checkout cost made a full inventory slow even for repositories whose page was small.

Some projects published versioned manpages only as release assets rather than files in the source tree.
Eza made the gap concrete: the matching release carried separate manpage and completion archives, and scanning the repository could not find the former.
The development system's handcrafted Mise `extra_assets` field named the expected pages, but using it for discovery would make MANIAC repeat a user's prior knowledge instead of discovering upstream documentation independently.

## Decision

Versioned remote manpage discovery will use a cached bare Git object store with no working tree.
It will fetch tree metadata without blobs, search the same bounded manpage locations and filename patterns used for local repositories, then retrieve and materialize only a selected manpage blob.
Repository cloning and checkout are removed from `list`'s availability path.
Exact tag resolution and binary-specific availability results will be cached under the same cache root and shared across concurrent probes.
Positive results for immutable versions may persist; negative results expire so a tag or release published later can become visible.

Remote documentation extraction will likewise avoid materializing unrelated repository files.
It may retain Git as its transport and cache, but paths outside the documentation trees it consumes will not be checked out.

For GitHub sources, the release matching the installed version will be searched independently for manpage files and archives.
Direct manpage assets, archives whose tokenized names indicate manpages, and conservatively sized archives are candidates for inspection; large unrelated binary bundles are skipped.
Release archives are accepted only after their members are inspected and an existing manpage pattern matches the installed binary; archive names alone do not establish availability, so completion-only archives remain excluded.
Once a bundle is anchored by that primary page, every valid manpage member in the same archive is retained, including differently named companion pages and other manual sections.
Mise `extra_assets` will not be read by this discovery path; handcrafted entries may be used only as regression expectations.

## Consequences

The inventory path transfers repository tree objects and a candidate page rather than a complete checkout, and filesystem filename limits no longer depend on unrelated repository content.
The cache is a Git object cache rather than a readable source checkout, so code that needs a directory must use the remote-object access layer instead of assuming repository files exist on disk.
Warm inventories avoid repeating remote tag and release lookups, while the negative-cache expiry makes a cold miss temporarily stale by design.

MANIAC can find authoritative pages that a repository-tree scan cannot find, including pages published only as release assets, without requiring a user-authored hint.
Companion pages inherit the provenance of a bundle anchored to the installed binary instead of being lost to binary-name filtering.
Release archive inspection adds a targeted network request and archive parser to discovery, but it avoids claiming a page from a suggestive asset name that contains only completions or unrelated files.

Remote sources that cannot provide the requested tag, tree, blob or asset still produce no tier-2 page rather than falling back to an unversioned guess, preserving ADR-0016's provenance rule.
