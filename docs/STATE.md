# Implementation State

Nothing is in flight.

[ADR-0020](adr/0020-login-shell-path-refuse-contextual.md) and [ADR-0021](adr/0021-login-path-resolution-version-readback.md) are implemented, verified live, and carry their own reasoning — including the finding that cost the most to reach, that a login shell *inherits* `$PATH` and appends to it rather than constructing one, so it reports the caller's answer unless its environment is scrubbed first.
[ADR-0019](adr/0019-earn-synthesized-page-version.md)'s last loose end is closed: `ty` was regenerated and its manifest entry records `version: "0.0.78"` where it previously recorded none.

Open work with no owner is in `docs/BACKLOG.md`.

## Baseline, for anyone comparing figures

Measured on the development system after ADR-0020 landed, at 398 tests green:

- `maniac list` returns 76 rows, identical from this repository and from `$HOME`.
- Two MANIAC-managed pages: `aichat` and `ty`, both carrying a recorded version, so both can now show `outdated`.
- One login-shell spawn per process — 61ms cold, 0.003ms cached.

**Figures recorded before ADR-0020 are not comparable to these.** They were taken through `uv run` with `.venv/bin` shadowing real binaries, which is exactly the distortion that work removed; the row count moved from 70 to 76 for that reason alone. Re-measure rather than trusting an older number.
