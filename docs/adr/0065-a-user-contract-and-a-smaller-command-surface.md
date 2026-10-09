# ADR-0065: Adopt a user contract and a smaller, more obvious command surface

Status: Accepted
Date: 2026-10-09

## Context

Between 2026-08-31 and 2026-10-09 the project recorded 64 ADRs, twelve of them on one day.
Seventeen are superseded and thirteen carry corrections, several of which reversed the decision itself on the day it was made.
The churn sits almost entirely in one place, unnamed `list`: "every tool on this machine and whether its page is right" is where maniac infers the most.
The questions were what counts as a tool, who owns a page and whether it is current:

- which `$PATH` to read flipped from login shell to inherited and back (ADR-0020, 0061, 0062);
- the `misattributed` state was added, narrowed and removed in three days (ADR-0052, 0055, 0056);
- which candidates `list` shows was decided six times in a row (ADR-0004 to 0010).

Most of the rules that came out of this decide scope, and apply silently:

- system binaries never appear (ADR-0059), yet `install ls` would quietly generate a page;
- a `.bashrc`-only `$PATH` hides tools (ADR-0062);
- a project-only shim vanishes (ADR-0063);
- a page with no recorded version reads `ok` forever (ADR-0019).

The help text spoke in internals ("tiers 1-2", "a provider detects") and still named `$(maniac status)`, renamed away by ADR-0018.

The user found every new question ("should update only handle managed pages?") turning into a decision of its own.
The cause was structural: no small set of rules existed that answered such questions mechanically, so each edge case was settled, and recorded, one at a time.

## Decision

[`docs/CONTRACT.md`](../CONTRACT.md) is the single statement of what maniac does, as a user can observe it.
Its four rules are:

1. maniac changes only pages it installed, or tools you name.
2. maniac documents the tool your login shell runs from `$HOME`, and says so when the current shell would run another copy (see Corrections for how a tool with no global copy is documented).
3. Every skip is counted, and every decision can be explained (`why <tool>`).
4. When the evidence is missing, maniac says `unknown`.

The command surface becomes `install`, `update`, `remove`, `list`, `scan` and `why`, with developer commands under a hidden `maniac dev`.
`list` reports only the pages maniac installed, so ownership stops being a filter.
Discovery across `$PATH` moves to `scan`, labelled best effort.
`source crawl` and `source docs` fold into `why`.

The vocabulary changes with it:

- page sources are named `shipped`, `upstream` and `generated` (formerly vendor, upstream and maniac);
- `--no-synthesize` becomes `--no-generate`;
- `unverified` becomes `unknown`, and also covers a managed page with no recorded version.

Decisions the user took on 2026-10-09:

- **Discovery stays**, as its own `scan` command, rather than being dropped or postponed.
- **Naming a project-only tool needs a flag** (see Corrections: `--force`).
  A named system binary is refused, and the refusal shows the page its package already ships (rule 1 with ADR-0059).
- **One contract document, and fewer ADRs.**
  An ADR is written only when a change alters the contract.
  Edge cases are decided by its rules and explained in the commit message.
  The existing ADRs stay as history.
- **Developer commands leave the main help.** `source` folds into `why`; `eval` and the benchmark move under `maniac dev`.

The redesign is built in phases tracked in `docs/STATE.md`, each leaving `just check` green.

## Consequences

The contract describes behaviour the code reaches phase by phase; until a phase lands, the README remains the description of what is live.

When a phase lands, it marks the ADRs it overrides as superseded in part:

- ADR-0013: no `update` command, and the meaning of unnamed `list`;
- ADR-0018 and ADR-0026: the state names;
- ADR-0027: the source labels;
- ADR-0047: the flag name;
- ADR-0059: named system binaries;
- ADR-0062: the divergence notice, and `--force` for a tool with no global copy.

The locally held draft that argued against an `update` command, branch `hold/update-path-adr-0065`, is abandoned.
Its fix giving every managed page a row in `list` becomes the basis of the new `list`.

Questions that would once have been new ADRs now go to the rules first.
A question the rules cannot answer is a gap in the contract, and closing it is an ADR.

## Corrections

2026-10-09: `--here` is gone; `--force` is the one override.

The Decision gave a project-only tool its own flag, `--here`, to keep `--force` to a single meaning.
The user found `--here` uncommon and hard to guess.
Read as "install where maniac would refuse", `--force` already has a single meaning, and its cases cannot pull against each other:

- a page maniac did not install at the destination: backed up and replaced;
- no global copy: the copy this shell runs is documented and recorded;
- a system package's binary: installed anyway, and the page it hides is named.

A project-only tool has no global copy to choose instead.
A foreign page at its destination is one more thing the user asked to install past.
Every override prints a `forced:` line.

What goes with `--here` is choosing a venv's copy over an existing global one.
maniac documents the global copy and notes the venv's, as rule 2 says a global page should.

The user chose that `--force` also covers system binaries: one override without an exception to remember.
ADR-0059's refusal now holds only without `--force`.
