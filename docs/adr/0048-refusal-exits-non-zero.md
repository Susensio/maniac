# ADR-0048: Exit non-zero whenever install produces no page, including a deliberate refusal

Status: Accepted
Date: 2026-09-16
Narrows: [ADR-0020](0020-login-shell-path-refuse-contextual.md)

## Context

[ADR-0020](0020-login-shell-path-refuse-contextual.md) gave `install` a refusal: a binary reachable only from the invoking shell does not get a global, permanent page, and the refusal says why rather than declining quietly.
It settled what the refusal means and what it must print.
It did not say what the process exits with.

The implementation that landed with it, `6ba4581`, answered that by omission.
`InstallRefused` was caught in the per-tool loop, printed in yellow, and — unlike the `OSError`/`RuntimeError`/`ManiacError` arm beside it — did not increment the `failures` counter that drives `typer.Exit(1)`.
A comment recorded the reasoning: "A refusal, not a failure: no tier ran, so nothing failed."

That reading holds inside the program and breaks at its boundary.
No tier ran, so nothing raised — true. But the user asked for a manual page and did not get one, and a shell sees only the exit status.
`maniac install ruff && echo installed` printed `installed` in exactly the case ADR-0020 wrote the refusal to make visible.
The refusal's whole purpose was to avoid reproducing the silent failure of ADR-0018, and against a script it reproduced it.

The question became live on 2026-09-16 rather than at ADR-0020, because a second refusal was added.
An install whose manpath destination already holds an unmanaged page now refuses before any tier runs, so that the user can re-run with `--force` and no generated artifact is left behind by a refused install.
Routing that through the existing `InstallRefused` path was locally consistent and moved the collision case from exit 1 — where a mid-pipeline `FileExistsError` had put it — to exit 0.
A case that used to fail loudly started succeeding quietly, which made the unstated convention worth stating.

## Decision

`install` exits non-zero whenever it produces no page for a tool named on the command line.
A deliberate refusal is included: refusing is a reason for the failure, not an exemption from it.

This covers ADR-0020's unreachable-binary refusal, the unmanaged-destination collision, and `--no-synthesize` finding nothing at tiers 1 and 2.
It is one rule rather than a judgement per refusal kind, because the distinction that matters to a caller is whether a page exists afterwards, and that is the same question in all three cases.

The rule is stated in terms of that outcome deliberately, because the three cases did not share a mechanism.
Implementing it on 2026-09-16 found that the first two raised `InstallRefused` while the third returned an ordinary outcome carrying no tier, and that the two were rendered alike only because both happened to print yellow.
A rule phrased as "what `InstallRefused` exits with" would have fixed two of the three and left the third exiting zero for the same reason as before.

Refusals keep the presentation ADR-0020 gave them.
They are still printed as refusals, in yellow, naming their reason, and still read differently from a crash — the exit status changes, the diagnostics do not.
A multi-tool invocation still attempts every tool it was given and reports per-tool outcomes; the status reflects whether any tool ended without a page.

`--dry-run` is not a refusal and is unaffected: a preview that writes nothing has done what was asked and exits zero.

ADR-0020's decision about *which* binaries to refuse is untouched. This settles only what the process reports afterwards.

## Consequences

The scripted case stops lying.
`maniac install x && ...` proceeds only when `x` has a page, which is what the conjunction claims and what ADR-0018's correction was about.

A refusal now costs a non-zero status in interactive use too, where it is noise: the user can see the yellow line and does not need the exit code to know what happened.
This is accepted because the interactive reader loses nothing — the message is unchanged — while the non-interactive caller gains the only signal it has.

The in-program reading that produced exit 0 was not wrong about the program; it was answering a different question from the one the exit status asks.
The rule is stated here in terms of the outcome the caller observes, so that the next refusal added does not have to re-derive it — which is the failure this record exists to prevent, since routing a new case through the old convention is exactly how the collision case changed status without anyone deciding to change it.

Any future refusal inherits the non-zero status by default.
A refusal that genuinely should exit zero would be a real exception to a stated rule and would have to supersede this record rather than quietly join the `InstallRefused` arm.
