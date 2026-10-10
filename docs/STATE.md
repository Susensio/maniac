# State: architecture maintenance

Committed to on 2026-10-10, after an architecture review with external tools (grimp, radon, complexipy, vulture, pylint, git history).
This file is the working progress report; it is deleted when the last phase lands.

## What the review found

The code is not complex locally: average cyclomatic complexity 3.8, one function past the complexity limit, almost no copy-paste, no import cycles but the CLI's command registration.
The cost is structural: a few core questions are each answered in several places, by different code returning different types, so one change touches five files.
In git history the same cluster changes together and takes most fixes: `sources/resolution.py`, `orchestration/context.py`, `listing/classification.py`, `orchestration/install.py`, `providers/mise.py`, `exceptions.py`.

1. **Which binary, who installed it, what version** is resolved five ways: `install` (`resolve_tool`), `why` (`_binary_section`, by hand), `scan <tool>` (`named_candidate`), `scan` (`enumerate_installations`, cognitive complexity 60) and `list` (`current_version`), each with its own result type and each catching its own subset of three exceptions used as control flow.
   125 test patches stub its functions.
2. **The source order** (shipped, upstream, generated) is written three times: `install` (`_select_page`), `why` (`_sources_section`), `scan` (`classify`, and `listing/upstream.py` with a different discovery function).
   The shipped and upstream tiers repeat one install skeleton, and only the upstream one prunes companion pages a release dropped.
3. **A managed page's state** is computed twice: `list` (`_page_row`) and `scan` (`classify`).
4. **Layering**: `orchestration` imports `listing`, and `sources`, `sources.docs` and `sources.providers` import each other.
5. **`scan`'s live table** has two builders for the same four columns (`_list_table`, `_streaming_table`).
6. **Dead code**: fields written and never read, functions only tests call, a config key nothing reads.

## The audit

`just audit` (`tools/audit.py`, settings in `audit.toml`) measures each finding and fails when one grows past its ceiling; it runs in `just check`, so in CI.
A phase is done when its metrics reach their targets; `just audit --update` then lowers the ceilings, so the gain cannot be lost.
Measured at the start:

| Metric | Start | Target | Phase |
|---|---|---|---|
| `dead_code` | 9 | 0 | A1 |
| `resolution_implementations` | 5 | 0 | A2 |
| `resolution_exceptions_handled` | 10 | 0 | A2 |
| `over_complex` | 1 | 0 | A2 |
| `page_state_implementations` | 2 | 1 | A3 |
| `source_order_implementations` | 4 | 1 | A4 |
| `layering` | 5 | 0 | A6 |
| `package_cycles` | 1 | 0 | A6 |
| `module_cycles` | 0 | 0 | guard |

## How each phase is verified

A1, A2, A3, A5 and A6 are refactors: observable behaviour must not change (CLAUDE.md).
Each is verified by `just check`, and by a behaviour snapshot: every command's output on a fixed set of tools (`install`, `list`, `update`, `scan`, `remove`, `why`), captured before the phase and compared after it.
A4 carries the one intended behaviour change (pruning), in its own commit, test first.

## Phases

### A0 The audit and this plan: done (2026-10-10)

- [x] `tools/audit.py`, `audit.toml`, `just audit` (ratchet) and `just audit-suggest` (complexipy's refactor hints, advisory); `audit` joins `check`.

### A1 Dead code: next

- [ ] Delete what nothing runs: `Config.max_arg_limit`, `EvaluationResult.deterministic_passed/defects`, `InstallOutcome.source_path/pipeline`, `discovery._extract_mise_tool_id`, `resolution.discover_repo`; tests that exist only to call them go, tests that cover behaviour through them call the live path instead.
- Done when `dead_code` is 0.

### A2 One resolution seam

- [ ] `locate(tool)` answers which binary the login `$PATH` reaches, through a mise shim, which installer claims it, at what version, whether it is a system binary, and the copy this shell runs if another, as one value: found, not on `$PATH`, a shim that runs nothing, not globally selected, or unreadable metadata. No exception escapes it.
- [ ] `install`, `why`, `list` and `scan` map that value to what they print; `enumerate_installations` becomes a walk over the login `$PATH` calling `locate`.
- [ ] `ResolvedTool` and `Candidate` are built from it, or replaced by it, whichever leaves fewer types.
- Done when `resolution_implementations`, `resolution_exceptions_handled` and `over_complex` are 0.

### A3 One page state

- [ ] One function gives a managed page's state, installed version, note and drift; `list`, `scan`, `why` and `install`'s already-current check call it.
- Done when `page_state_implementations` is 1.

### A4 One source order

- [ ] The source list is written once: each source yields an attempt (found, none, or a check that did not complete). `install` installs the first hit, `why` prints every attempt, `scan` maps the first to `available` or `missing`; `scan` and `install` ask upstream the same way.
- [ ] One function installs a found set of pages, whatever its source.
- [ ] Behaviour change, own commit, test first: a shipped release that drops a companion page prunes it, as an upstream one does.
- Done when `source_order_implementations` is 1.

### A5 One table builder for `scan`

- [ ] The final and live tables come from one builder (rows, pending rows, widths).
- The live view stays (decided by default; dropping it for a progress bar is the user's call, and would make this phase mostly deletion).

### A6 Layering

- [ ] Shared types and page state move below `orchestration` and `listing`, so neither imports the other.
- [ ] The `sources` / `sources.docs` / `sources.providers` cycle is broken by moving what both sides use to one place.
- [ ] Package moves only where they remove a cycle or a layering violation, as pure moves.
- Done when `layering` and `package_cycles` are 0.

## Log

- 2026-10-10: A0 landed: the review, this plan, `just audit` with today's numbers as ceilings.
