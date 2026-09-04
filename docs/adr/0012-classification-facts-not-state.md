# ADR-0012: Store manpage classification facts instead of a single derived state

Status: Accepted
Date: 2026-09-04
Supersedes: [ADR-0010](0010-default-subcommand-probes.md)

## Context

ADR-0010 classified a page as Help2man-generated or not, a binary read of a marker in the first 8 KiB, and used it to decide which candidates `list-missing --include-candidates` would show.
Generalizing that to other generators broke the rule in two ways, both checked against real pages on the development system.

The marker does not determine quality.
`ls.1` and `fold.1` are both Help2man-generated; `ls.1` carried 942 words of prose spliced in through Help2man's `--include`, `fold.1` carried 142.
A marker-only rule classifies them identically, and one of the two answers is wrong.

Generators that emit no marker exist, including the case that prompted this work.
`gum`'s page is produced by `mango-kong` over `muesli/roff`, neither of which writes a comment; the page is identifiable only structurally — empty `DESCRIPTION`, no `.\"` comments anywhere, an ISO-date-only `.TH`, and a body consisting entirely of `.TP` flag entries.
A survey of the active manpath found reliable comment markers for Help2man, `Pod::Man`, Pandoc, DocBook XSL, txt2man and po4a, and none for the mango/roff family.

Naming a single state for the outcome was attempted five times — `improvable`, `stub`, `generated`, `help-derived`, `bare` — and rejected each time.
The reason emerged only afterwards: the useful answer depends on two independent facts, the page's own condition and whether MANIAC has sources to do better.
A thin page for a tool with no upstream documentation warrants a different answer from a thin page for a tool with a documented repository, and no single word carries both.

Generators were also found to split by what their input was rather than by whether a machine ran.
Pandoc, `Pod::Man`, `go-md2man` and DocBook XSL convert prose a human wrote; Help2man, Cobra's `GenMan` and mango/roff synthesize from a flag parser.
Treating "machine-generated" as the axis would have flagged good pages.

## Decision

The classifier stores facts and never a verdict.
The facts are: whether a page exists and where; whether MANIAC authored it; which generator produced it; a measure of the prose it carries; which sources MANIAC can reach for the tool; and, once ADR-0011's version recording exists, the version the page was generated against.
Generator origin is internal and is not presented to the user.

The verdict — what should be done about a page — is a pure function over those facts, computed at display time and stored nowhere.
Its vocabulary is deliberately not settled by this record; it is to be chosen against real output from a populated cache rather than in the abstract.

The cache is keyed on `(path, mtime, size)` and holds facts only.

## Consequences

The verdict layer can be rewritten, and its words renamed, without invalidating a single cache row; caching verdicts instead would have discarded the whole cache on a rename.
The cache self-invalidates exactly when a tool is upgraded, which is when reclassification is wanted, and it is the same store the upgradeable-manpages entry in `docs/BACKLOG.md` requires.

Structural scoring will misclassify pages that carry no marker.
The bias is toward under-claiming: a false positive replaces a page a human wrote, while a false negative leaves one page unimproved, so an undecidable page is treated as authored.
This is a deliberate asymmetry and will leave some poor pages unflagged.

Separating reachable sources from page condition costs a source resolution per poor page, which is what distinguishes a tool MANIAC can help from one it cannot.
ADR-0010's default subcommand probing is retained; measurement on the development system showed the full scan completing in seconds, the one-second bound being a timeout rather than a per-command cost.
