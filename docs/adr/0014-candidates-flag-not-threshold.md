# ADR-0014: Select candidates with a boolean flag instead of exposing the heuristic or naming a page state

Status: Accepted
Date: 2026-09-05

## Context

ADR-0012 stored classification facts and deferred the verdict layer, stating that its vocabulary was to be chosen against real output rather than in the abstract.
ADR-0013 left the filter `status` accepts unsettled for the same reason.
This record settles both, against a dump of 5504 pages from the development system.

Naming a page state was attempted seven times and rejected every time: `improvable`, `stub`, `generated`, `help-derived`, `flags-only`, `bare`, and a `poor`/`good` tier.
The objections were consistent and different each time -- a judgement rather than an observation, jargon, a collision with MANIAC's own generated pages, and a word the user did not recognise.
The diagnosis only became clear once the facts were separated from the verdict: the useful answer depends on two independent facts, the page's condition and whether MANIAC has sources to do better, and no single word carries both.

Real output then showed no single number orders the corpus either.
Words per flag entry isolates the motivating case (`gum`, 986 flag entries at 3.9 words each) but inverts `ls` at 15.3 against `fold` at 32.0, and is undefined for pages reporting no flag entries at all.
Of the 15 pages on the development system with a resolvable source, a threshold of 15 words per flag entry selected `gum`, `gh`, `pastel` and `just`, leaving `usage` at 18.1 just outside; the nearest unselected page was `zoxide` at 36.2, a gap of roughly two, once MANIAC's own pages were excluded by ownership rather than by score.

Exposing the threshold as a command-line option (`--max-words-per-flag 15`) was weighed and rejected: it publishes an internal heuristic as interface, so the heuristic can never change without breaking a user's pipeline, and it requires the user to understand the measure in order to filter.

## Decision

`status` presents observations, never a verdict word.
The columns report what was measured -- flag-entry count, word count, page ownership -- so the reason a row was selected is visible without a vocabulary standing between the user and the facts.

A single boolean `--candidates` selects the pages the internal heuristic flags.
The term is the one ADR-0008 and ADR-0010 already use for a page MANIAC believes it could improve, so it introduces no new vocabulary, and it names a selection rather than passing judgement on the page.

The heuristic and its threshold are internal.
The threshold ships in the packaged `defaults.toml` under `[classification]` and is overridable through the user's `config.toml` on ADR-0011's chain, so it is tunable without appearing in `--help`.

The heuristic applies only where the page's dialect is understood and a flag-entry count is therefore meaningful; where it is not, the page is reported with its observations and no selection, per ADR-0012's bias toward under-claiming.

## Consequences

The threshold can be retuned, and the heuristic replaced outright, without changing the command line or breaking a pipeline.
Nothing in the interface has to be renamed if the measure changes, because the interface never named the measure.

A single boolean cannot express "show me pages that are borderline", and a user wanting a different threshold must edit configuration rather than pass an option.
The observation columns are what make that tolerable: a borderline page is visible in the table even when `--candidates` does not select it.

Selection depends on dialect detection, so a page in an unrecognised dialect is never selected, however poor it is.
