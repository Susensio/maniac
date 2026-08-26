# Audit findings — 2026-08-26

Produced by a three-arm review experiment (Claude auditor, Claude+agy, agy alone), reconciled and adjudicated by an opus `auditor` against the working tree at `ccad03b` plus uncommitted changes.
Every finding below survived adjudication against the code.
Confidence is marked: **reproduced** means the auditor executed it; **read** means confirmed by reading or grep.

Nothing here was fixed. The tree was left untouched.

---

## First, the test suite is not actually broken

`just test` is **90/90 green on a bare runner**.
It goes red in your shell only because `FORCE_COLOR=3` is set globally in your environment.
That is H1 below — the tests are environment-dependent, not failing.

---

## High

### H1 · `tests/test_cli.py:153,164` — assertions split by ANSI codes
**reproduced** — `FORCE_COLOR` unset → 2 passed; `FORCE_COLOR=3` → 2 failed.

Root cause is `maniac/cli.py:23`, `Console(highlight=True)`.
Rich injects colour, the assertions match on plain substrings, and the substring is broken up by escape sequences.

*Fix direction:* pin the console for tests (`Console(force_terminal=False, no_color=True)` in a fixture), or assert against `Text.plain` rather than raw output.

### H2 · `maniac/evaluation/judge.py:436,488` — `--min-score` can only raise the bar
**reproduced** — score 60 with `--min-score 50` reports FAILED.

`parse_evaluation_json` sets `passed=False` when the key is absent, and `run_llm_judge`'s `if score < threshold` only ever downgrades.
Nothing re-raises `passed` when the score clears a *lower* threshold, so any `--min-score` below 70 is inert.

*Fix direction:* derive `passed` from `score >= threshold` at the point of comparison rather than trusting the parsed value.

### H3 · `maniac/templates/eval_prompt.md:54` — threshold hardcoded in the prompt
**read**

The template literally contains `<boolean_true_if_score_>=_70_and_no_critical_flaws>`, and `build_evaluation_prompt` takes no threshold argument.
So `--min-score` never reaches the judge at all; the model is always told 70.
Compounds H2 — even fixing the Python leaves the model applying the wrong bar.

*Fix direction:* pass the threshold into the template and interpolate it.

---

## Medium

| # | Location | Problem | Confidence |
|---|---|---|---|
| M1 | `orchestration/pipeline.py:35-38` | Tests write into your real `~/.local/state/maniac/` | reproduced |
| M2 | `installer.py:96-99` | Prints "✓ Uninstalled" while a foreign vendor page remains in `man_dir` | reproduced |
| M3 | `installer.py:87-92` | A file restored from backup is reported as "Removed"; `tests/test_installer.py:114-118` asserts both `in removed` **and** `.exists()`, locking the bug in | reproduced |
| M4 | `sources/docs.py:63` | `MAX_TOTAL_DOC_CHARS=100_000` contradicts `Config`'s `75_000` | read |
| M5 | `config.py:48-51` | Four timeout fields are read nowhere; live values hardcoded at `generation/llm.py:18`, `evaluation/judge.py:149`, `evaluation/judge.py:296` | read |
| M6 | `pyproject.toml:26-28` | No `lint.select`, so `just check` is not "all checks" — ruff reports **39** E501 lines once enabled | read |
| M7 | `sources/docs.py:73` | `mkdir` runs before the `is_local` branch, leaking `~/.cache/maniac/repos`; `tests/test_docs.py:51,93` inherit it | read |
| M8 | `scripts/evaluate_all_matrix.py:107` | Overwrites `results.json` wholesale and drops skipped rows (the `continue` skips `final_results.append`) | read |
| M9 | `sources/discovery.py:38` | Fallback builds `https://github.com/<tool>.git` with no owner — always fails to clone | reproduced |
| M10 | `cli.py:260` | `batch` swallows every failure and always exits 0 | read |
| M11 | `installer.py:107` | `--purge` leaves `{tool}_prompt.md` orphaned | read |
| M12 | `evaluation/judge.py:283,324,355` | Raw markdown labelled "WHAT THE USER SEES IN `man`" — `render_manpage_to_terminal` returns unrendered text when pandoc is absent, and the label still claims it is rendered | read |
| M13 | `evaluation/judge.py:141` | A missing `pandoc` binary is counted as a *document* defect, failing the manpage for a host problem | read |

---

## Low

Grouped by kind, all confirmed by reading.

**Dead or unreachable code**
- `DiscoveryError` is defined and never raised.
- `sources/docs.py:177` — the `UnicodeDecodeError` arm is unreachable given `errors="replace"`.
- `get_help`'s `timeout` parameter is never passed by its sole caller, `sources/crawler.py:204`.
- `generation/prompts.py:18` — the fallback directory does not exist.

**Test fragility**
- `tests/test_llm.py:42` — wrong type annotation.
- `tests/test_llm.py:56` — stubs `shutil.which` for *every* name, so which branch runs depends on the host.

**Style / clarity**
- `sources/docs.py:141-159` — nesting depth.
- `orchestration/pipeline.py` — numbered comments.
- `installer.py:25` — permission-denied case is ambiguous.
- `compute_coverage` is recomputed three times.
- `sources/docs.py:130` and `:152` disagree.
- `sources/discovery.py:107`.
- `cli.py:66` is 243 characters.

---

## Claims that did **not** survive

Recorded so they are not re-raised.

- `sources/crawler.py:205` "silently returns partial results" — **false**. It logs at `:178` and `:181`, and re-raises at `:207` when nothing was collected.
- "README divergence: this area is clean" — **false**. Contradicted by H3 and by a gap in the Prerequisites section.

---

## Suggested order

1. **H2 + H3 together** — `--min-score` is documented and broken in two places at once; fixing one alone still misapplies the threshold.
2. **M1 + M7** — tests writing into your real XDG dirs. Cheap to fix, and they contaminate every later run.
3. **H1** — unpin colour in tests so the suite stops depending on your shell.
4. **M3** — then delete the assertion that locks it in.
5. **M6** — turn on `lint.select`, then work the 39 E501s.
6. Everything else as it comes up.
