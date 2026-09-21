# ADR-0049: Collapse `list` rows on a proven shared target, not a shared package

Status: Accepted
Date: 2026-09-21

## Context

`_grouped_for_display` collapses rows sharing `(provider, package, state, source, upstream)` into one, labelled by the sole member's tool name or, for several, `<package> (<n> binaries)`.
[ADR-0018](0018-list-reports-manpage-reachability.md) recorded this behaviour without deciding it.
"Separately, `_grouped_for_display` labelled a solo group by its tool name and a multi-binary group by its package name... That defect is recorded in `docs/BACKLOG.md` and is not decided here."
It named `pandoc`, `pandoc-lua` and `pandoc-server` as [ADR-0016](0016-authoritative-manpages-first.md)'s founding case for treating binaries within one install root as distinct: three separate manuals, one package.

Three defects, all traced live on the development machine in September 2026 and all recorded in `docs/BACKLOG.md`, share one root cause.
The key treats "same package" as license to collapse, when the actual claim a collapsed row makes is "these names are one thing."

1. **Label collision.** The unfiltered table renders `python (7 binaries) | missing` above `python (4 binaries) | unverified` with no label distinguishing them.
   `python` sharing a package with `pip`, `idle3` and `pydoc3` does not make them the same program.
2. **Wrong Source link.** A group's hyperlink is the representative row's page alone.
   `page_path`/`page_uri` are not in the key, so 5 of 68 binaries on the live inventory link somewhere other than the row standing for them — `pandoc-lua.1.gz` under `pandoc (3 binaries)`, `node`'s own claim shadowing `npm.1`.
3. **Wrong owning-package label.** The 2026-09-21 Source work (`2f51106`) surfaced a page's provable Debian owner in place of bare `system`.
   The same representative-only reasoning mislabels it exactly as it mislabels a link: `python (4 binaries) unverified` shows `python3.12-minimal` — `python`'s own owner — for the whole row, while `pydoc3` is actually owned by `python3.12` and `python3-config` by `libpython3.12-dev:amd64`.

A fourth, separately filed defect is not decided here: the table sorts by wherever the group's earliest-alphabetical *member* falls, not by its rendered label (`docs/BACKLOG.md`, confirmed live 2026-09-16).
Narrowing what collapses changes how many labels exist to sort, so this record unblocks that fix without performing it.

**What "same program" actually requires, established against real evidence this session, not assumed:**

`python`, `python3` and `python3.14` are one file.
`Installation.real_path` — already computed by resolution, already stored on every `Installation` — is identical across all three: each name's `$PATH` lookup resolves through mise's `~/.local/bin` symlink layer to the same install-root binary.
Comparing `real_path` needs no new resolution work.

`pip`, `pip3` and `pip3.14` are not symlinks.
They are three separate files holding byte-identical content — confirmed, `sha256sum` agrees on all three — because Python's `console_scripts` packaging writes one real file per configured entry-point name and never symlinks them.
`real_path` does not unify these; each resolves to itself, so a second rung, a content hash, is required to prove three distinct files are one program.
Hashing is checked only after `real_path` fails to unify, and only within one `(provider, package)` partition, so the common case — an ordinary symlink alias — never pays for it, and a 32MB binary like `python3.14` is never hashed at all: `python`/`python3`/`python3.14` already unify on `real_path` alone before hashing would ever be reached.

Neither rung is the `--version`-output comparison [ADR-0020](0020-login-shell-path-refuse-contextual.md) left unbuilt.
That comparison was not rejected in principle — ADR-0020 rejected falling further down `$PATH` on no evidence at all ("unclaimed does not distinguish a transparent wrapper from a genuinely different build").
Filesystem identity is evidence a `--version` string match only approximates.

**Same target does not imply same reachability answer — the gotcha that shapes the decision, not a simplifying assumption.**
`python3.14` shares `real_path` with `python` and `python3` and still must not collapse with them.
`man -w python3.14` finds no page on this machine at all, while MANIAC's own managed manpath makes it reachable regardless, landing it `ok`/`vendor` against `python`/`python3`'s `unverified`/`system`.
An identity-based rule that collapsed on target alone would merge a working row into a broken one.
Reachability is looked up per name, independently, and two names sharing a target can still get different answers.

## Decision

The group key gains two components and loses none: `(provider, package, state, source, page_path, owning_package, target_cluster)`.

`page_path` — the `Path` MANIAC already resolved per row, compared directly, no `realpath` needed since two independent lookups landing on the same file already produce the same resolved path, as `python`/`python3` do — closes defects 2 and 3 outright, with no new evidence.
`pandoc`, `pandoc-lua` and `pandoc-server` already carry three distinct `page_path`s, as do `python`/`python3` versus `pydoc3` versus `python3-config`.
Adding it to the key is the direct implementation of the already-filed "key a display group on its members' pages" item.
The owning-package mislabel turned out to be the identical bug wearing D's new field; no separate mechanism was needed for it.

`target_cluster` is computed once per `(provider, package)` partition, over members whose `page_path` already agrees — never across a page-identity split, since a differing page already proves two different things regardless of what their binaries resolve to.
Group by `installation.real_path` first; within any remaining un-clustered members, group by content hash of the resolved file.
This is the rung `page_path` cannot supply on its own — it is what defect 1 needs, because `idle3`, `pip`, `pydoc3` and `python3.14-config` all show `state=missing`, `page_path=None`, and `None == None` proves nothing about whether they are one program or four.

`target_cluster` is computed only among candidates already sharing `(provider, package)`; it refines the existing partition and never merges across packages.
A candidate with no `Installation` — ADR-0020's unclaimed-binary case — never needs it: `Candidate.package` falls back to the tool's own name when unclaimed, so an unclaimed binary already partitions alone under the existing key.

A collapsed group's label is its shortest member's name, tie-broken alphabetically.
A version-suffixed name churns on upgrade (`python3.14` becomes `python3.15` next release); the unsuffixed name persists and is what a reader actually types.
This replaces "solo row keeps its tool name, multi-row takes the package name" — the mechanism ADR-0018 recorded and declined to defend — with one rule for every group size.

`_TOOL_COLUMN_MAX_WIDTH` (24) is unchanged and is the binding constraint on this choice.
A comma-joined member list was considered and rejected on exactly this budget, consistent with the existing rejection of that shape for the same column.

**Considered and rejected: bridge a differently-named package to a common upstream identity** — e.g. reading dpkg's `Homepage`/`Source:` fields to prove `python3.12-minimal` and mise's `python` are the same software, so the `outdated` state, not merely a label, could be claimed.
Investigated live: `python3.12-minimal`'s `Homepage` is empty, and its `Source:` field gives `python3.12`, which would require stripping a Debian versioned-source-package suffix to compare against `python`.
That is the same shape of guess this project has already been burned by three times — `fmt` → `nushell/nufmt`, `od` → `todo.txt-cli`, `envsubst` → `a8m/envsubst` — just applied to a package name instead of a binary name.
Rejected; the 2026-09-21 Source work's provable-owner display (`2f51106`) and the roff-header verifier (`0518507`) cover this ground with evidence instead of a heuristic, and neither is superseded by this record.

## Consequences

`python (7 binaries) missing` splits into however many `target_cluster`s its members actually form — provisionally `{idle3, idle3.14}`, `{pip, pip3, pip3.14}`, `{pydoc3.14}`, `{python3.14-config}`, unconfirmed until implemented and measured against the live inventory.
`python (4 binaries) unverified` splits on `page_path` alone, with no clustering needed, into `{python, python3}`, `{pydoc3}`, `{python3-config}`.
The unfiltered table gains rows; this is the point, not a regression.
A package sharing a state across unrelated binaries was always the noise this record exists to remove, not a summary worth preserving.

Every group's Source link and owning-package label are now correct by construction: a group can only exist where every member already agrees on `page_path`, so there is no representative's-page-only case left to be wrong.
The Source-link and owning-package-mismatch items in `docs/BACKLOG.md` close as a consequence of this key change; neither needs its own fix.

The sort-order defect (`docs/BACKLOG.md`, first confirmed 2026-09-16) is unblocked, not fixed.
More distinct labels exist post-split than before; the fix already diagnosed there — sort after grouping, by the rendered label — applies to whatever set of labels this key produces.

`Candidate`/`Installation` gain no new fields.
`target_cluster` is computed at display time from `real_path` (already stored) and, rarely, a content hash — computed, not stored, `functools.cache`d process-lifetime at most, consistent with `sources/packages.py`'s existing `_debian_owner`/`_debian_version` caching, and never persisted to the manifest.
No behavior outside `list`'s table changes: `install`, `uninstall` and the manifest are untouched, since `Entry.group` (ADR-0042/0046) is a different, already-distinct concept from a display group and this record does not touch it.
