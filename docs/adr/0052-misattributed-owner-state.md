# ADR-0052: Add a misattributed state for a page a package manager positively proves the wrong owner of

Status: Accepted
Date: 2026-09-21

## Context

ADR-0026 gave `list` a five-state action ladder and a Debian adapter,
`verify_external_page`, that proves a page's owning package and version
before calling it `ok` or `outdated`. Its `unverified` branch fires in two
situations that `verify_external_page` cannot currently tell apart:
`dpkg-query -S` finding no owner at all, and `dpkg-query -S` finding an
owner that provably differs from the binary's own installing package.

The live case that exposed this: `man python` on a mise-managed system
serves `/usr/share/man/man1/python3.12.1.gz`, a Debian page `dpkg-query -S`
attributes to `python3.12-minimal`, while the `python` binary on `$PATH`
is mise's `python`, version `3.14.7`. The page's own `.TH` header carries
no version (`docs/BACKLOG.md`), so `verify_page_header` cannot reach the
case either, and `list` renders the row `unverified` -- the same label a
system with no package manager at all would get for the same binary.
That is honest but understates what is known: `dpkg-query` did not fail to
find evidence, it found evidence that contradicts the installation. Losing
that distinction inside `unverified` throws away the stronger of the two
findings ADR-0026's own adapter already computes.

`ExternalPageVerification.owner` already carries the owning package
whenever `dpkg-query -S` can name one, independent of `freshness`, so the
information distinguishing the two cases already exists at the call site;
only the freshness value collapses them.

`verify_page_header` (`maniac/sources/roff.py`) exists for a narrower
purpose: proving freshness from a page's own header when `dpkg` "cannot
own" the page at all (its own docstring names `verify_external_page`'s
`UNVERIFIED` case as its reason to run). It has no way to establish
ownership, only a version match against a title that already named the
right binary. `_external_page_state` currently invokes it for every
`UNVERIFIED` freshness result before falling back to `ActionState.UNVERIFIED`,
regardless of which of the two situations produced it.

## Decision

`ExternalPageFreshness` gains a fourth value, `WRONG_OWNER`, returned by
`verify_external_page` exactly when `_debian_owner` names an owner and that
owner's package identity differs from the binary's installing package --
the branch that today falls into `UNVERIFIED` alongside "no owner found".
The value lives in `ExternalPageFreshness`, not only in `ActionState`,
because the distinction is evidence quality established at the adapter
that did the lookup; threading a `owner is not None` check into
`_external_page_state` instead would force every future freshness
consumer to re-derive the same branch from `owner`, still not knowing
whether the running package manager was even consulted. `verify_page_header`
keeps returning only `MATCH`/`MISMATCH`/`UNVERIFIED`: it never establishes
ownership, so it has no basis to assert `WRONG_OWNER` and is not changed.

`_external_page_state` (`maniac/listing/classification.py`) stops routing
`WRONG_OWNER` through the roff-header fallback. A parsed version match in
the page's own header proves the page documents the version it claims; it
says nothing about which package the page belongs to, and `dpkg-query` has
already positively answered that question the other way. Running the
fallback here would let a coincidental version match downgrade or hide a
proven misattribution -- the false-positive risk `verify_page_header`'s own
docstring already flags for confirming freshness, mirrored onto refuting
ownership instead. The fallback still runs for the true `UNVERIFIED` case,
where `dpkg` has nothing to say at all.

`ActionState` gains `MISATTRIBUTED`, mapped from `WRONG_OWNER` the way
`OK`/`OUTDATED`/`UNVERIFIED` already map from `MATCH`/`MISMATCH`/`UNVERIFIED`.
It is colored yellow in `maniac/cli/listing.py`, the same band as
`OUTDATED`: both name a page proven wrong that a free install or reinstall
can replace, as opposed to `MISSING`'s red (needs an LLM) or `OK`'s green
(needs nothing). It joins the `--outdated`/`--unverified`/`--available`/
`--missing` state filters as `--misattributed`.

## Consequences

The `python`/`man python` row now reads `misattributed` instead of
`unverified`, carrying the owning package (`python3.12-minimal`) the same
way `outdated` already does, and scripts filtering on `unverified` no
longer see it. Any other row where `dpkg-query -S` names a real but
differing owner reclassifies the same way; the ladder grows by one public
value and one filter flag, mirroring ADR-0026's own growth over ADR-0018.

The version-header fallback narrows: previously any `UNVERIFIED` result,
including a proven wrong owner, got a second chance at `ok`/`outdated`
through the page's own header. Now only a true absence of ownership
evidence does. A wrong-owner page whose header happens to carry a matching
version no longer surfaces as `ok` -- the header cannot rebut what `dpkg`
already proved, so `misattributed` stands even when the roff header,
if it were consulted, would have said otherwise.

Only Debian's adapter can produce `WRONG_OWNER` today, the same asymmetry
ADR-0026 already accepted for `MATCH`/`MISMATCH`: a system without a
supported native adapter still reads `unverified`, never `misattributed`.
