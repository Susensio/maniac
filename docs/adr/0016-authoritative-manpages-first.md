# ADR-0016: Prefer authoritative manpages over synthesis and rename generate to install

Status: Accepted
Date: 2026-09-07
Supersedes: [ADR-0014](0014-candidates-flag-not-threshold.md)

## Context

MANIAC treated synthesis as the only way to produce a manual page.
`run_pipeline` crawled `--help`, fetched repository documentation, called an LLM, compiled the result with Pandoc and installed it.
Every tool the tool could act on was routed through that path.

Inspection of the development system showed the assumption was wrong for a large share of tools.
Upstream manual pages were already present on disk, inside the installation itself, at exactly the installed version:
`fzf/0.71.0/fzf.1`, `just/1.58.0/just.1`, `zoxide/0.9.9/man/man1/`, `pastel/.../man/`, and 220 files under `gh/2.90.0/.../share/man/man1/`.

`pandoc` was the case that made the gap concrete.
Its mise install root held `pandoc.1.gz`, `pandoc-lua.1.gz` and `pandoc-server.1.gz`, while `man pandoc` reported no manual entry.
Three authoritative pages, written by the project, matching the installed version, unreadable because nothing had linked them onto the manpath.
The common failure was not that a tool ships no documentation; it was that the installation method did not put what it ships where `man` looks.

The existing helper for this could not have found them.
`find_repo_manpage` globbed `<bin>.[1-9]`, which does not match `pandoc.1.gz`, and searched only a repository root and its `man`/`doc`/`docs` trees, which does not reach `share/man/man1/`.
It was dead code, called from nothing, so neither gap had surfaced.

An installed-manpage source also removes a risk that the synthesis path carries.
Documentation fetched from a repository is read at the default branch, so a generated page can describe flags the installed binary does not have.
A page taken from the install root is the installed version by construction.

Against that, the machinery for deciding whether an existing page is bad enough to replace had grown expensive relative to what it delivered.
ADR-0014 exposed candidate selection as a `--candidates` flag over a configured `min_words_per_flag` ratio.
That ratio requires a recognised dialect and a countable flag entry, and abstains otherwise, so `bat` and `fish-lsp` — which mark options with `.HP` rather than `.TP` — were unrateable and silently absent from the candidate list however poor their pages were.
Two measured attempts at counting `.HP` entries reliably had already failed.

Separately, the command surface had an orphan.
`uninstall` removed a MANIAC-generated page and restored the vendor backup, and nothing was named as its inverse; the operation that created the page was called `generate`.

## Decision

Documentation sources are tried in a fixed order, cheapest and most authoritative first:

1. the install root, where the page is the installed version by construction;
2. the upstream repository or another online source, with the version matched to the installed one;
3. synthesis from `--help` and fetched documentation.

An authoritative page found at tier 1 or 2 is installed as it stands.
Whether such a page is good enough to keep is not asked at this stage.

`generate` is renamed `install`, making it the inverse of `uninstall`.
Source selection is overridden by two flags on that command rather than by separate commands: `--generate` restricts it to tier 3, and `--no-generate` restricts it to tiers 1 and 2, never calling an LLM.

Judging an existing page's quality, and replacing a page that is merely poor, leave the current scope.
`--candidates` and the `min_words_per_flag` threshold that ADR-0014 introduced are removed with them.
`status` reports what MANIAC can act on — a shipped page not yet installed, no page at all, or a page MANIAC manages — and no longer reports a quality verdict.

Classification facts are unaffected.
ADR-0012 stores facts and defers the verdict; this decision defers the verdict further rather than reversing it.

## Consequences

The most common useful action becomes free.
Installing a page that upstream already wrote costs no LLM call, no network request and no version reconciliation, and it is more faithful than synthesis, because upstream wrote it.

The product's own claim narrows honestly.
MANIAC generates where documentation does not exist, and provisions where it does.

Tier 2 carries a risk tier 1 does not.
A page fetched from a wrongly resolved repository and installed verbatim is worse than a wrongly sourced generated page, because it carries no hedge and reads as official.
Tier 2 must therefore check that the page matches the binary it claims to document and that the version corresponds, and it inherits the unresolved install-source-versus-documentation-source problem recorded against ADR-0015.

What to do when no upstream version matches is not settled here.
Refusing is consistent with resolving nothing by guesswork, but its cost in lost coverage has not been measured, and measuring it is deferred until providers are in place.

Removing `--candidates` breaks the `maniac status --candidates | xargs maniac generate` pipeline that ADR-0013 composed.
The pipe survives, with a different selection and a renamed command.

Dropping quality judgement leaves a real gap: a poor vendor page that MANIAC could improve is now invisible, including the `.HP`-style pages that were already invisible under ADR-0014.
This is a deferral, not a conclusion that the work is worthless, and it is recorded as a future milestone in `docs/BACKLOG.md`.

`--generate` and `--no-generate` describe the same axis from opposite ends, which is a small redundancy accepted for legibility: forcing synthesis and forbidding it are both things a user wants to say plainly.
