# ADR-0055: Prove same software from Debian's own naming, and require positive disproof for a wrong owner

Status: Accepted
Date: 2026-09-22
Amends: [ADR-0026](0026-verify-external-page-freshness.md)
Narrows: [ADR-0052](0052-misattributed-owner-state.md)

## Context

ADR-0026 required that a verifier "prove that the page-owning package and the detected installation describe the same package before comparing their versions", and ruled out three things as evidence: directory location, repository-name suffixes, and binary-name similarity.
ADR-0052 then added `WRONG_OWNER`/`MISATTRIBUTED` for the case where `dpkg-query -S` names an owner that "provably differs" from the binary's installing package.

Neither ADR specified how sameness is established.
`verify_external_page` (`maniac/sources/packages.py`) settled it with string equality: `_package_name(owner) != _package_name(package)` yields `WRONG_OWNER`.

That test compares two different namespaces.
`owner` is Debian's package name; `package` is the provider's own identity, and every provider sets it from its own naming — mise from the installs-path segment (`python`, `tealdeer`), cargo from the crate name, npm from the `node_modules` segment, uv and pipx from their tool and venv directory segments.
The two namespaces agree only by coincidence.
`tealdeer` matching Debian's `tealdeer` is that coincidence, not a design; nothing makes it hold for the next tool.

So the comparison inherits ADR-0026's own prohibition from the other side.
ADR-0026 forbids reading name similarity as evidence of sameness; the implementation reads name *dissimilarity* as proof of difference.
Neither direction is evidence, and the second is what reaches the user as a positive claim that a page belongs to some other package.

The live consequence on this machine is three false positives.
`man python`, `man pydoc3` and `man python3-config` resolve to Debian pages owned by `python3.12-minimal`, `python3.12` and `libpython3.12-dev:amd64`.
Debian embeds the interpreter's minor version in the package name, so mise's tool_id `python` never string-matches any of them, and all three rows read `misattributed`.
They are the same software.
Worse, the correct answer was available and was being discarded: mise's python is 3.14.7 and the Debian pages document 3.12, which is `outdated` — the exact state ADR-0026 built the version comparison to produce.

### What the evidence permits

Two investigation passes against this machine established what dpkg can and cannot prove.

`${Source}` collapses split binary packages to their source package: `python3.12-minimal` and `libpython3.12-dev` both give `python3.12`, and `tealdeer` gives `rust-tealdeer`.
That is strictly better evidence than the binary package name, and it is still not the provider's name.

`${Provides}` is empty on all three Python owners.
Where it is populated it names abstract virtuals — `gcc-13` provides `c-compiler` — not the upstream project.
`apt-cache showsrc` is unusable here: no `deb-src` entries are configured, and it reaches no further than `${Source}` in any case.

`${Homepage}` is empty on all three Python owners and on their `${Source}` package, so it cannot decide the case that prompted this ADR, though it is populated on 84% of installed packages and 86% of those owning a page under `/usr/share/man`.

On MANIAC's own side there is frequently nothing to compare against at all.
For mise's `python`, the registry gives the backend `core:python`, and `_mise_entry_repo` (`maniac/sources/discovery.py`) returns a repository only for `github:` and `aqua:` backends, so no upstream identity is resolved whatsoever.
Where both sides do carry a URL they may still disagree while naming one project: dpkg records tealdeer's homepage as `dbrgn/tealdeer`, mise's registry as `tealdeer-rs/tealdeer`, an org migration.
GitHub resolves both to repository `48739367`.

## Decision

Sameness is established by two mechanisms, and `WRONG_OWNER` requires positive disproof.

**Debian's mechanical naming is admitted as evidence of sameness.**
The comparison runs against `${Source}` rather than the binary package name, then normalizes it through a short set of rewrites for the naming Debian generates mechanically: the interpreter version suffix (`python3.12` to `python`), the language-team prefixes (`rust-`, `golang-github-<owner>-`, `node-`, `haskell-`, `ruby-`), and the split-package suffixes (`-minimal`, `-dev`, `-doc`, `-common`, `-bin`, `-data`).
Exact equality is tried first and is unchanged.

This amends ADR-0026, and the warrant deserves stating honestly rather than overclaiming.
These are packaging-team conventions, documented across the Debian Python, Rust and Go packaging guides rather than in one normative specification, and they are followed by convention rather than enforced by the archive.
The argument for admitting them is that they are *generated*, not *resembled*: Debian derives `python3.12-minimal` from Python by a rule, and reading that rule backwards recovers a fact rather than guessing at a likeness.
The argument against is that a convention no tool enforces will have exceptions, and a wrong rewrite attributes another build's documentation to a tool — precisely the harm ADR-0026's prohibition exists to prevent.
That risk is accepted, bounded by keeping the rule list short, requiring a real package on this machine behind every entry, and refusing anything speculative.
Rewrites are not open season on name manipulation: a rewrite must correspond to a naming scheme Debian actually applies, and nothing else qualifies.

**Failure to prove sameness yields `UNVERIFIED`, not `WRONG_OWNER`.**
This is the narrowing of ADR-0052.
When the names do not match after normalization, what has been established is that dpkg found an owner MANIAC cannot tie to the installation — not that the owner is wrong.
`UNVERIFIED` states exactly that, and the roff-header fallback ADR-0052 routed around `WRONG_OWNER` becomes reachable for these rows again, which is correct: a header version match cannot rebut a proven misattribution, but there is no longer a proven misattribution to rebut.

**`WRONG_OWNER` fires only on positive disproof: two distinct canonical repository identities.**
Where the owning package's `${Homepage}` and MANIAC's resolved upstream both name a GitHub repository, each resolves to its canonical numeric repository ID by following GitHub's redirect, which is what makes `dbrgn/tealdeer` and `tealdeer-rs/tealdeer` recognizable as one repository rather than two.
Converging IDs prove sameness and fall through to the version comparison.
Two distinct IDs prove difference, and only that produces `WRONG_OWNER`.
Absent evidence on either side, or a network failure, yields `UNVERIFIED`; a row is never failed on an unreachable network.
The lookup is gated behind name comparison failing to decide and both sides carrying a URL, and cached per process, keeping ADR-0026's rule that package-manager work stays off the enumeration path.

ADR-0052's state, its yellow band and its `--misattributed` filter all survive unchanged.
Only the trigger narrows, from an inference to a proof.

### Alternatives rejected

A per-tool alias map (`python` to `python3.12-minimal`) decides every case and is unbounded, needing an entry per tool per distribution, with nothing generating it.

`${Provides}`, `${Source}` or `${Homepage}` used alone each fail on the exact packages that prompted this: `${Provides}` empty, `${Source}` still version-suffixed, `${Homepage}` empty.
`${Source}` is retained as the input to normalization rather than as a decision on its own.

Full reversal of ADR-0052 — deleting `MISATTRIBUTED` and returning every unproven row to `UNVERIFIED` — is the most conservative reading of ADR-0026 and was rejected because it discards a real distinction.
dpkg naming an owner is genuinely different from dpkg finding nothing, which was ADR-0052's actual contribution and remains true.

Relabelling the state to claim only that an owner was found but could not be matched was rejected as strictly weaker than re-founding it on proof, once canonical repository identity was found to supply that proof.

## Consequences

The three Python rows read `outdated` instead of `misattributed`, reaching the state ADR-0026's version comparison was built to produce.
`tldr` reaches `outdated` by rule — through `rust-tealdeer` normalizing to `tealdeer` — rather than by the namespace coincidence it depended on before, so the row survives Debian renaming its package.
No row on this machine reads `misattributed`, and `maniac list --misattributed` is empty.

`MISATTRIBUTED` becomes rare.
It now requires an owning package with a populated `${Homepage}`, a resolved upstream on MANIAC's side, and the two resolving to different repositories.
No installation on this machine meets that, so the disproof path ships correct by construction and unexercised by any real row, covered only by unit tests against recorded redirect responses.
That is a known gap, accepted because the alternative is leaving the state founded on an inference ADR-0026 rules out.

A fork counts as positive disproof, which this decision accepts without qualifying it.
A fork has its own repository ID, so where Debian's `${Homepage}` names the canonical upstream and a provider registry names an actively maintained fork of the same tool, the two IDs diverge and the row reads `misattributed`.
That follows from "two distinct IDs prove difference" as written, and it points the damaging way: proving difference wrongly rather than failing to prove sameness.
It is recorded here as a known edge of the rule rather than a defect in it, because no installation on this machine is fork-shaped and the alternative -- reading GitHub's `fork` and `parent` fields -- adds a second inference to a decision whose whole point was to stop inferring.
`docs/BACKLOG.md` carries it for the day a fork-shaped row appears.

The rewrite list is a maintenance surface that will grow as Debian's ecosystems do, and each addition is a small amendment to this ADR's warrant rather than a free change.
A rewrite that proves wrong will misattribute rather than merely fail to attribute, which is the more damaging direction, so additions want a real package behind them and a test.

Freshness verification now costs a `${Source}` lookup per verified row on top of the existing owner and version queries, and, for the narrow gated case, one network round trip.
Both are cached per process and neither touches provider enumeration.

Only Debian's adapter is affected.
A system without a supported native adapter still reads `unverified` and can still never read `misattributed`, the same asymmetry ADR-0026 and ADR-0052 already accepted.
