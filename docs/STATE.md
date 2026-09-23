# Implementation State

Work committed to and not yet finished.
Landed work lives in `docs/adr/` and the git history; it is removed from here once it lands, so this file stays short enough to read before starting.

## Live-system facts worth not rediscovering

Cross-host redirect stripping cannot be exercised end to end, and this is not a gap to close.
Release assets are fetched from `browser_download_url`, a `github.com` URL, so they never carry the header to strip.
It is defence in depth against a future caller authenticating against a redirecting endpoint, not a path production reaches today, so unit tests are the only coverage it can have.
The independent review of the `gh` credential work ran on 2026-09-16 and passed: no credential can reach a non-GitHub host on any path in the tree.
Its three low findings were closed in `5a3d0f7` -- the host allowlist grew regression tests for the suffix and subdomain lookalikes, the redirect handler now compares scheme as well as host so a same-host `https`-to-`http` downgrade drops the header, and an environment-supplied token is stripped rather than passed through raw.

The development machine is the only MANIAC installation in existence -- solo tool, solo developer, pre-1.0.
Its manifest holds two entries, `aichat` and `ty`, both `tier=synthesis` with targets set.
That is why both historical migrations were deleted rather than fixed: neither could ever have fired.

Mise links into `~/.local/bin` rather than using shims on this machine, so a `$PATH` lookup for `python` hits `~/.local/bin/python`, a symlink to `../share/mise/installs/python/3.14.7/bin/python3.14`.
Two rungs of evidence are needed to tell one program under several names from several programs.
`python`, `python3` and `python3.14` collapse under `readlink -f`, which resolves all three to one file.
`pip`, `pip3` and `pip3.14` do not -- they resolve to three distinct paths holding byte-identical content (sha256 `6b2d4f13...`), because Python packaging writes `console_scripts` entry points as real files, one per configured name, and never symlinks them.
So symlink identity alone under-collapses every Python-packaged tool, and content hash is the rung that catches it; neither is the `--version` guessing ADR-0020 rejected.
Twelve names in that bin directory are five distinct programs.

The system python is not shadowed away entirely: `/usr/bin/python` and `/usr/bin/python3` lose to `~/.local/bin`, but `python3.12` has no mise counterpart and resolves to `/usr/bin/python3.12` on its own.

Provider package names and Debian package names are different namespaces, and any agreement between them is coincidence.
Every provider sets `inst.package` from its own naming -- mise the installs-path segment, cargo the crate name, npm the `node_modules` segment.
`tealdeer` matching apt's `tealdeer` was luck, not design, and `python` never matching `python3.12-minimal` was the same luck running out (ADR-0055).
What rescues it is `${Source}`, which collapses split packages to one name -- `python3.12-minimal` and `libpython3.12-dev` both give `python3.12`, and `tealdeer` gives `rust-tealdeer`, confirmed live on this machine.
Normalizing that through Debian's mechanical naming reaches the provider's name; normalizing the binary package name does not, because the `lib` prefix survives (`libpython3.12-dev` would reach `libpython`).

After ADR-0055 phase 1 (`9302dff`), `python`, `pydoc3`, `python3-config` and `tldr` all read `outdated`.
The `misattributed` state is gone entirely (ADR-0056, `454b861`), and an owner that cannot be tied to the installation reads `unverified` with the owning package still shown beside it.

The measurement that killed it is worth not retaking.
Of 601 installed packages owning something under `/usr/share/man`, 516 carry a `${Homepage}` and only 82 -- 15.9% -- name a `github.com` URL; the rest are metacpan (56), gnu.org (33), freedesktop.org (24), wiki.gnome.org (23), kernel.org (13).
Of 80 `maniac list` rows here, five have a Debian-owned page at all, and all five normalize to a match under phase 1 before any identity check is reached.
Mise's 1008-entry registry crossed against those packages yields exactly one genuine same-name-different-software collision: Debian's `coreutils` is GNU, mise's resolves to `aqua:uutils/coreutils`.
It is invisible to a GitHub-identity test because Debian's homepage for it is `gnu.org` -- the evidence source was blind to its own motivating example, which is the fact that settled the question.
A green suite says nothing about that: the states are only visible by running `maniac list` in a real terminal, because piped output degrades to bare names (ADR-0018's accepted accident) and the Live view truncates rows below roughly 220 columns.
Verifying a state change means a wide tmux pane, not a pipe.

`man` under the agent sandbox reads a different manpath than the user's shell, so `maniac list` verified there is not the table the user sees.
`~/.local/share/man` lists fine but cannot be opened -- `man --debug -w python3` reports "can't open directory ... Permission denied" and falls through to `/usr/share/man` without saying so.
Sandboxed, `man -w python3` answers `/usr/share/man/man1/python3.12.1.gz` and `python3` groups with `python` as one `outdated` row; unsandboxed it answers mise's `python3.14.1` and `python3` groups with `python3.14` as `ok`/`vendor`.
Both were reproduced from the same commit on 2026-09-23, which is how a grouping defect was diagnosed that did not exist.
Any `maniac list` claim about reachability has to come from an unsandboxed run, and a verification that silently loses a manpath entry is indistinguishable from a classification change.

The 2026-09-21 owning-package work (`2f51106`) exposed three different real owners inside that one `python (4 binaries) unverified` group, confirmed live: `python`/`python3` are owned by `python3.12-minimal`, `pydoc3` by plain `python3.12`, `python3-config` by `libpython3.12-dev:amd64` (dpkg's raw output, architecture qualifier included -- `_package_name()` strips it only for the comparison, not for display).
That group no longer exists: after ADR-0055 those four are `outdated` and the display splits them, because the grouping key is a 7-tuple including `page_path`, `state`, `source` and `owning_package` (`maniac/cli/listing.py:141-149`), so a group cannot render a state or owner a member lacks.
The four grouped rows today are `idle3` (2), `pip` (3), `python3` (2) and `tree-sitter` (2), and every member of each agrees on all four fields -- audited member-by-member on 2026-09-23.
What is rendered from the representative alone and is *not* in the key is `upstream` and `page_uri`; no group's members disagree on those today, so it is theoretical.

`python3` and `python3.14` group together and read `ok`/`vendor` because both resolve to mise's own page, `~/.local/share/man/man1/python3.14.1`.
`python` is not with them, and the reason is the useful one: there is no `python.1` in MANIAC's managed manpath, so the bare name falls through to Debian's `python3.12.1.gz` and reads `outdated` on its own evidence.
Executable identity does not imply the same reachability answer, let alone the same page -- `python`, `python3` and `python3.14` are one file under `readlink -f` and get two different pages -- which is why the grouping rule checks the resolved answer and not merely the target.

There is no populated Mise shim directory here, so shim discovery is unverified and `mise which -C $HOME` remains cwd-sensitive.
A live `mise activate bash` exports `MISE_SHELL`, `__MISE_EXE`, `__MISE_DIFF` and `__MISE_ORIG_PATH`.

Four stamped pages under `~/.local/share/maniac/manpages` carry provenance headers; tier-2 pages carry none, which is what lets recovery tell the two apart (ADR-0046).

A structural manifest scan costs ~0.046 ms per entry -- 0.157 ms for the live two-entry manifest, against an 11.8 s `maniac list`.
It walks what MANIAC owns, not what is on `$PATH`.

## Conventions learned the hard way

`monkeypatch.setattr` on a `structlog` logger attribute does not survive its own teardown.
`logger` is a `BoundLoggerLazyProxy`, so `monkeypatch` reading the old `.debug` to restore it later goes through `__getattr__` and binds a concrete logger; teardown then restores that bound method rather than undoing the shadow, freezing the attribute for the rest of the process.
That is what leaked `List inventory timing` into piped CLI output and made `tests/test_inventory.py` plus `tests/test_listing.py` fail only when run together (`a670951`).
Use `structlog.testing.capture_logs()`, which swaps the processor list and never touches the proxy.
A `structlog.reset_defaults()` autouse fixture now bounds this suite-wide, because the reverse order failed for the mirror-image reason: a prior CLI test's `setup_logging()` left the level at WARNING for a test that needed it permissive.

A test stub that cannot occur in production hides the bug it is standing in for.
`maniac install`'s silent-success defect -- a compile failure leaving no page while exiting 0 and printing bold green -- survived because two CLI tests stubbed a *successful* synthesis with `installed_path=None`, a combination `pipeline.py` cannot produce.
The suite was green on a state the program never reaches, so nothing pinned the state it does.
The same shape hid an uninstall reporting bug twice over: one test stubbed `removed` and `changed` both populated, which that path cannot do.
When a test builds a result object by hand, check the producer can actually emit that combination.

Green is not reviewed, for uninstall and exit codes especially.
Three rounds were needed on the 2026-09-16 install/uninstall rework: two independent reviews each returned FAIL against a green suite, and every finding of the second was the first round's defect class still reachable one path over.
A fix written against a reported reproduction tends to close that reproduction and not the class.
Ask which neighbouring paths reach the same code before calling one closed, and where two places encode one rule, collapse them rather than patching both.

A subagent told `docs/` is out of scope may still `git checkout` an uncommitted change there, reading it as stray.
Commit records before dispatching work that touches the same tree, or expect to rewrite them.

Three sessions allocated ADR numbers concurrently in September 2026 and collided twice; the manifest transaction ADR moved from 0044 to 0046.
Allocate a number when the decision lands, not when the work starts.

`master` is shared and has had commits from more than one session at a time, including a rewind that dropped an entire branch's work.
Check `git merge-base --is-ancestor` before moving it, and gate the move on that check rather than merely printing its result.
