# ADR-0056: Remove the misattributed state; an owner that cannot be tied to the installation is unverified

Status: Accepted
Date: 2026-09-23
Supersedes: [ADR-0052](0052-misattributed-owner-state.md)
Amends: [ADR-0055](0055-prove-same-software-before-naming-a-wrong-owner.md)

## Context

ADR-0052 added `MISATTRIBUTED` for a page a package manager positively proves the wrong owner of.
ADR-0055 then found that the implementation proved no such thing: it read name inequality across two unrelated namespaces as proof of difference, which is the mirror of the inference ADR-0026 forbids.
Phase 1 of ADR-0055 fixed the defect by proving sameness through Debian's mechanical naming, and narrowed `WRONG_OWNER` to require positive disproof.
Phase 2 supplied that disproof: two distinct canonical GitHub repository IDs, resolved by following the API redirect so a rename and a difference could be told apart.

Phase 2 shipped correct and unexercised, which ADR-0055 recorded as an accepted gap.
A reachability measurement taken immediately afterwards showed the gap is not a gap in testing but in the mechanism's reach.

Of the 601 installed packages owning something under `/usr/share/man`, 516 carry a `${Homepage}` and only 82 of those -- 15.9% -- name a `github.com` URL.
The rest point at metacpan (56), gnu.org (33), freedesktop.org (24), wiki.gnome.org (23), kernel.org (13) and similar.
A GitHub homepage on the *Debian owner* is a precondition for the disproof, so five sixths of the manpage-owning archive can never reach it.

Of the 80 rows `maniac list` produces on this machine, five have a Debian-owned page at all, and all five normalize to a *match* under ADR-0055 phase 1 before the identity check is ever consulted.
The conjunction `MISATTRIBUTED` requires -- a provider-claimed binary, a Debian-owned page, a name mismatch surviving normalization, a GitHub homepage on the owner, a GitHub upstream resolved for the tool, and two genuinely different repositories -- is met by nothing here, and `maniac list --misattributed` is empty.

The decisive fact is the near-miss.
Cross-referencing mise's 1008-entry registry against the manpage-owning packages found exactly one genuine same-name-different-software collision: Debian's `coreutils` is GNU coreutils, while mise's registry entry for `coreutils` resolves to `aqua:uutils/coreutils`, the Rust reimplementation.
That is precisely the case `MISATTRIBUTED` exists to catch, it exists on this machine, and the mechanism cannot see it -- Debian's homepage for `coreutils` is `gnu.org`, not GitHub.
The evidence source has a blind spot shaped like its own motivating example.

One argument for keeping the state has also expired.
Full reversal was rejected while deciding ADR-0055 because it would discard the distinction between dpkg naming an owner and dpkg finding nothing, which was ADR-0052's real contribution.
Phase 1 made that argument obsolete: unproven sameness now returns `UNVERIFIED` with `ExternalPageVerification.owner` still populated, because `owner` is set independently of `freshness`.
The distinction is carried by the owner field and survives the state's removal.
`MISATTRIBUTED` adds a label, not information.

## Decision

`ExternalPageFreshness.WRONG_OWNER` and `ActionState.MISATTRIBUTED` are removed, together with the `--misattributed` filter, its colour band, and the README's description of the state.
An external page whose owner cannot be tied to the installation reads `unverified`, and the owning package continues to be reported beside it.

ADR-0055 phase 2 is reverted with them: `_debian_homepage`, `_github_identity`, `_wrong_owner_disproof` and `canonical_github_repository_id` are deleted, and the `upstream` parameter is un-threaded from `classify`, `_resolved_page_classification`, `_external_page_state` and `_classify_and_resolve`.
Nothing else consumes those, and a parameter threaded through four frames to feed a deleted branch is exactly the bound-but-unused state the pre-1.0 rule in `CLAUDE.md` says to remove rather than keep for a caller that does not exist.

ADR-0055 phase 1 stands unchanged and is not affected.
It fixed a real defect -- three rows misreporting `misattributed` that should read `outdated` -- and its rewrite rules are what decide every real row on this machine.
This ADR reverses only the half that was built to re-found a state now being removed.

The roff-header fallback in `_external_page_state` reverts to running for every `UNVERIFIED` result, which is what ADR-0026 specified before ADR-0052 carved the exception out.
The exception existed to stop a coincidental header version match from hiding a proven misattribution; with no proven misattribution left to hide, it has nothing to protect.

### Alternatives rejected

Keeping the state and widening its evidence -- treating two populated non-GitHub homepages on clearly different hosts as disproof -- would catch the `coreutils` case and lift coverage well above 15.9%.
It was rejected because it is closer to URL comparison than to identity proof, and this decision has already amended ADR-0026's evidence rule once; a second loosening onto weaker evidence is the same move that produced the original defect.

Removing the state from the CLI while keeping the machinery for a future re-add was rejected as strictly worse than either alternative: it leaves dead code and an unused parameter threaded through four functions, which the pre-1.0 rule argues against more strongly than it argues against deletion.

Recording the finding and changing no code was rejected because an unreachable state in the documented state ladder misleads every future reader about what `list` can tell them.

## Consequences

The action ladder returns to ADR-0026's five states.
Scripts filtering on `--misattributed` break; nothing on this machine uses it, and it was introduced two days ago.
No row's rendered state changes, because no row reaches `misattributed` today -- this removes a label nothing produces, not a behaviour anyone observes.

Three ADRs in three days have now touched this state: ADR-0052 added it, ADR-0055 narrowed it, ADR-0056 removes it.
That churn is worth naming rather than hiding.
ADR-0052 was decided from one observed row without asking how often the conjunction it needed could occur, and the reachability measurement that settles the question was cheap and could have been taken first.
The lesson is the measurement, not the reversal: a new state on the public ladder should come with a count of how many rows can reach it before it ships, not after.

MANIAC loses the ability to report a proven wrong owner.
On the evidence gathered this is a capability it never actually had, since the only real candidate is invisible to the mechanism.
Revisit if a row ever appears whose page is Debian-owned, whose name mismatch survives phase 1's normalization, and where both sides resolve to different GitHub repositories -- the `git log` for ADR-0055 phase 2 (`1e35eb3`) carries a working implementation to restore, and `docs/BACKLOG.md` keeps the fork question that would need answering alongside it.
